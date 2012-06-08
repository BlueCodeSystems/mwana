from mwana.apps.smgl.tests.shared import SMGLSetUp, create_prereg_user
from mwana.apps.smgl.models import Referral
from mwana.apps.smgl.app import FACILITY_NOT_RECOGNIZED, REFERRAL_RESPONSE


class SMGLReferTest(SMGLSetUp):
    fixtures = ["initial_data.json"]
    
    def setUp(self):
        super(SMGLReferTest, self).setUp()
        Referral.objects.all().delete()
        self.user_number = "123"
        self.name = "Anton"
        self.createUser("worker", self.user_number)
        
    def testRefer(self):
        self.assertEqual(0, Referral.objects.count())
        # bad code
        bad_code_resp = FACILITY_NOT_RECOGNIZED % { "facility": "notaplace" }
        script = """
            %(num)s > refer 1234 notaplace
            %(num)s < %(resp)s            
        """ % { "num": self.user_number, "resp": bad_code_resp }
        self.runScript(script)
        
        self.assertEqual(0, Referral.objects.count())
        
        success_resp = REFERRAL_RESPONSE % {"name": self.name, 
                                            "unique_id": "1234"}
        script = """
            %(num)s > refer 1234 804024
            %(num)s < %(resp)s
        """ % { "num": self.user_number, "resp": success_resp }
        self.runScript(script)
        
        [referral] = Referral.objects.all()
        self.assertEqual("1234", referral.mother_uid)
        