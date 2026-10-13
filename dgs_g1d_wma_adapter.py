"""Adapter WMA del laboratorio. Nessun DDS o robot fisico.
Contratto observation provvisorio, da verificare con lab_server.py reale.
"""
import json
import logging
import math
import os
import time
import uuid
from typing import Any, Dict
import requests

WMA_BASE_URL = os.getenv('WMA_BASE_URL', 'http://10.32.55.4:8000').rstrip('/')
WMA_ENDPOINT = os.getenv('WMA_ENDPOINT', '/predict_action')
TIMEOUT = float(os.getenv('WMA_TIMEOUT', '120'))
SIMULATOR_URL = os.getenv('SIMULATOR_URL', 'http://simulator:6003').rstrip('/')
MAX_OBSERVATION_AGE = float(os.getenv('MAX_OBSERVATION_AGE', '10'))
logger = logging.getLogger('DGS-G1D-WMA-Adapter')


def simulator_to_wma(simulator_state: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(simulator_state, dict):
        raise ValueError('Observation deve essere un oggetto JSON')
    return {'observation': simulator_state}


def call_wma(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = WMA_BASE_URL + WMA_ENDPOINT
    logger.info('Invio richiesta WMA a %s', url)
    try:
        response = requests.post(url, json=payload, timeout=(3, TIMEOUT), allow_redirects=False)
        logger.info('WMA HTTP %s', response.status_code)
        if not 200 <= response.status_code < 300:
            return {'status': 'ERROR', 'http_status': response.status_code, 'error': 'WMA HTTP error', 'raw_response': response.text[:65536]}
        try:
            data = response.json()
        except ValueError:
            return {'status': 'ERROR', 'error': 'WMA ha restituito contenuto non JSON', 'raw_response': response.text[:65536]}
        if not isinstance(data, dict):
            return {'status': 'ERROR', 'error': 'WMA JSON deve essere un oggetto', 'raw_response': data}
        # Reject NaN/Infinity even when accepted by a permissive JSON decoder.
        json.dumps(data, allow_nan=False)
        return data
    except (requests.RequestException, ValueError) as exc:
        return {'status': 'ERROR', 'error': str(exc)}


def wma_to_simulator(wma_response: Dict[str, Any]) -> Dict[str, Any]:
    # Preserves the envelope of the user's script. This alone is NOT executable.
    return {'source': 'UnifoLM-WMA-0-Dual', 'action': wma_response}


def process_observation(simulator_state: Dict[str, Any]) -> Dict[str, Any]:
    return wma_to_simulator(call_wma(simulator_to_wma(simulator_state)))


def map_command(response):
    """Opt-in lab contract: {command:{action:...,params:{...}}}.
    Does not guess meanings of model tensors, joint ordering or 'stand'.
    """
    if not isinstance(response, dict) or response.get('status') == 'ERROR':
        raise ValueError('Risposta WMA in errore')
    cmd = response.get('command')
    if not isinstance(cmd, dict) or set(cmd) - {'action', 'params'}:
        raise ValueError('Mapping richiesto: command con action e params; nessun campo di identita o policy')
    action, params = cmd.get('action'), cmd.get('params', {})
    schemas = {'status': set(), 'stop': set(), 'move': {'v','w','duration'},
               'height': {'value'}, 'joint': {'name','value'}, 'gripper': {'state'}}
    if not isinstance(action, str) or action not in schemas or not isinstance(params, dict) or set(params) - schemas[action]:
        raise ValueError('Comando WMA non supportato')
    required = {'move': {'v','w','duration'}, 'height': {'value'}, 'joint': {'name','value'}, 'gripper': {'state'}}
    if not required.get(action, set()) <= set(params):
        raise ValueError('Parametri obbligatori mancanti')
    for key, value in params.items():
        if key in {'v','w','duration','value'} and (isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value)):
            raise ValueError('Parametro numerico non finito: '+key)
    if action == 'joint' and not isinstance(params['name'], str):
        raise ValueError('Nome joint non valido')
    if action == 'gripper' and params['state'] not in ('open','closed'):
        raise ValueError('Stato gripper non valido')
    return {'action': action, 'params': params}


def run_cycle(instruction='stand', execute=False):
    if not isinstance(instruction, str) or not 1 <= len(instruction) <= 2000:
        raise ValueError('instruction deve essere una stringa di 1..2000 caratteri')
    if not isinstance(execute, bool):
        raise ValueError('execute deve essere booleano')
    result = {'cycle_id': str(uuid.uuid4()), 'execute_requested': execute, 'applied': False}
    started = time.monotonic()
    try:
        r = requests.get(SIMULATOR_URL+'/state', timeout=5)
        r.raise_for_status()
        state = r.json()
        observation = {**state, 'instruction': instruction}
        result['observation'] = observation
        result['wma_payload'] = simulator_to_wma(observation)
        result['simulator_command'] = process_observation(observation)
        response = result['simulator_command']['action']
        if response.get('status') == 'ERROR':
            result.update(status='WMA_ERROR', error=response.get('error', 'WMA error'))
            return result
        try:
            command = map_command(response)
        except ValueError as exc:
            result.update(status='UNMAPPED', error=str(exc))
            return result
        result['mapped_command'] = command
        if not execute:
            result['status'] = 'PREVIEW'
            return result
        if time.monotonic()-started > MAX_OBSERVATION_AGE:
            result.update(status='STALE', error='Observation troppo vecchia: comando non inviato')
            return result
        # Identity and PEP route are assigned locally, never taken from model output.
        command = {**command, 'request_id': result['cycle_id'], 'timestamp': time.time(), 'identity': 'operator', 'via': 'pep', 'target': 'g1d-01'}
        r = requests.post(SIMULATOR_URL+'/command', json={'payload':command,'principal':'operator'}, timeout=5)
        r.raise_for_status()
        result['pep_result'] = r.json()
        result['applied'] = result['pep_result'].get('decision') == 'ALLOW'
        result['status'] = 'APPLIED' if result['applied'] else 'BLOCKED'
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        # Never retry an action automatically: a timeout may follow its execution.
        result.update(status='TRANSPORT_ERROR', error=str(exc))
    finally:
        result['elapsed_ms'] = round((time.monotonic()-started)*1000, 1)
    return result


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--instruction', default='stand')
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    print(json.dumps(run_cycle(args.instruction, args.execute), indent=2, allow_nan=False))
