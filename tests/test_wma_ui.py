import os, unittest
from pathlib import Path
import test_wma as fixtures
try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest=None

@unittest.skipIf(AppTest is None,'Installare requirements-test.txt per verificare anche Streamlit')
class HarnessUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PipelineTests.setUpClass()
        cls.old_url=os.environ.get('ADAPTER_URL')
        os.environ['ADAPTER_URL']=f'http://127.0.0.1:{fixtures.PipelineTests.ap}'
    @classmethod
    def tearDownClass(cls):
        fixtures.PipelineTests.tearDownClass()
        if cls.old_url is None:os.environ.pop('ADAPTER_URL',None)
        else:os.environ['ADAPTER_URL']=cls.old_url
    def test_harness_preview_then_explicit_execution(self):
        fixture=fixtures.PipelineTests();fixture.setUp()
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'services/probe/app.py')).run()
        self.assertFalse(app.exception)
        self.assertFalse(app.checkbox[0].value)
        app.button[0].click().run(timeout=10)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['result']['status'],'PREVIEW')
        fixtures.PipelineTests.reply_data={'command':{'action':'move','params':{'v':.1,'w':0,'duration':1}}}
        app.checkbox[0].check().run()
        app.button[0].click().run(timeout=10)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['result']['status'],'BLOCKED')
        fixture.twin('/api/command',{'payload':{'action':'arm'}})
        app.button[0].click().run(timeout=10)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['result']['status'],'APPLIED')
