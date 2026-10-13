import json, os, urllib.request, urllib.error
import streamlit as st
st.set_page_config(page_title='G1-D WMA Test Harness',layout='wide')
st.title('G1-D → DGS Adapter → UnifoLM-WMA')
st.caption('Twin cinematico CPU • inference sul DGX • un ciclo per richiesta')
st.info('Contratto WMA provvisorio: observation in ingresso; command esplicito in uscita. Vettori/action non riconosciuti vengono solo registrati.')
url=os.getenv('ADAPTER_URL','http://adapter:6001')
instruction=st.text_area('Instruction',value='stand',max_chars=2000)
execute=st.checkbox('Esegui il comando riconosciuto attraverso il PEP',value=False)
st.caption('Per il movimento arma prima il twin sulla console :3000. Il modello non può armare o rimuovere E-stop.')
if st.button('Esegui ciclo WMA',type='primary'):
    try:
        req=urllib.request.Request(url+'/cycle',data=json.dumps({'instruction':instruction,'execute':execute}).encode(),headers={'Content-Type':'application/json'})
        with st.spinner('In attesa del WMA…'):
            with urllib.request.urlopen(req,timeout=135) as r:result=json.load(r)
        st.session_state['result']=result
    except (OSError,ValueError) as exc:st.error(str(exc))
if 'result' in st.session_state:
    result=st.session_state['result'];st.subheader(result.get('status','Risultato'));st.json(result)
    st.download_button('Scarica evidenza JSON',json.dumps(result,indent=2),'wma-cycle.json','application/json')
if st.button('Stato adapter / ultimo ciclo'):
    try:
        with urllib.request.urlopen(url+'/last',timeout=5) as r:st.json(json.load(r))
    except (OSError,ValueError) as exc:st.error(str(exc))

st.markdown('Camere simulate VM7: [Testa](https://10.32.55.9:60001) · [Polso SX](https://10.32.55.9:60002) · [Polso DX](https://10.32.55.9:60003)')
st.caption('Le immagini sono disponibili separatamente; non sono aggiunte al payload WMA finché il suo formato immagini non è confermato.')
