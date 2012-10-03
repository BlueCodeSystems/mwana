from mwana.apps.smgl.tests.shared import SMGLSetUp
from mwana.apps.smgl.models import DeathRegistration
from datetime import date, datetime, timedelta
from mwana.apps.smgl import const

class SMGLDeathRegTest(SMGLSetUp):
    fixtures = ["initial_data.json"]
    
    def setUp(self):
        super(SMGLDeathRegTest, self).setUp()
        self.user_number = "123"
        self.name = "Anton"
        self.createUser("worker", self.user_number)
        DeathRegistration.objects.all().delete()
        
    # TODO: beef these up. Just testing the basic workflow
    def testBasicDeathReg(self):
        resp = const.DEATH_REG_RESPONSE % {"name": self.name }
        script = """
            %(num)s > death 1234 01 01 2012 ma h
            %(num)s < %(resp)s            
        """ % { "num": self.user_number, "resp": resp }
        self.runScript(script)
        [reg] = DeathRegistration.objects.all()
        self.assertEqual("1234", reg.unique_id)
        self.assertEqual(date(2012, 1, 1), reg.date)
        self.assertEqual("ma", reg.person)
        self.assertEqual("h", reg.place)
        
    def testDODInPast(self):
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        script = """
            %(num)s > death 1234 %(date)s ma h
            %(num)s < %(resp)s            
        """ % { "num": self.user_number, "date": tomorrow.strftime("%d %m %Y"), 
                "resp": const.DATE_MUST_BE_IN_PAST % {"date_name": "Date of Death", 
                                                      "date": tomorrow}}
        self.runScript(script)
        
    def testDeathNotRegistered(self):
        script = """
            %(num)s > death 1234 01 01 2012 ma h
            %(num)s < %(resp)s
        """ % {"num": "notacontact", "resp": const.NOT_REGISTERED}
        self.runScript(script)
        
    def testBadDate(self):
        resp = const.DATE_NOT_NUMBERS 
        script = """
            %(num)s > Death 999999 rh dr rrrr inf f
            %(num)s < %(resp)s            
        """ % { "num": self.user_number, "resp": resp }
        self.runScript(script)
        self.assertEqual(0, DeathRegistration.objects.count())
        