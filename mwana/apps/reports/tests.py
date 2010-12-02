import time

import mwana.const as const
from datetime import date
from datetime import timedelta
from django.conf import settings
from mwana.apps.hub_workflow.models import HubSampleNotification
from mwana.apps.labresults.models import Result
from mwana.apps.labresults.models import SampleNotification
from mwana.apps.labresults.testdata.reports import *
from mwana.apps.locations.models import Location
from mwana.apps.locations.models import LocationType
from mwana.apps.reminders.models import Event
from mwana.apps.reports import tasks
from mwana.apps.reports.models import DhoReportNotification
from rapidsms.tests.scripted import TestScript


class SmsReportsSetUp(TestScript):

    def setUp(self):
        # this call is required if you want to override setUp
        super(SmsReportsSetUp, self).setUp()
        self.type = LocationType.objects.get_or_create(singular="clinic", plural="clinics", slug=const.CLINIC_SLUGS[2])[0]
        self.type1 = LocationType.objects.get_or_create(singular="district", plural="districts", slug="districts")[0]
        self.type2 = LocationType.objects.get_or_create(singular="province", plural="provinces", slug="provinces")[0]
        self.luapula = Location.objects.create(type=self.type2, name="Luapula Province", slug="400000")
        self.mansa = Location.objects.create(type=self.type1, name="Mansa District", slug="403000", parent=self.luapula)
        self.samfya = Location.objects.create(type=self.type1, name="Samfya District", slug="404000", parent=self.luapula)
        self.mibende = Location.objects.create(type=self.type, name="Mibenge Clinic", slug="403029", parent=self.samfya, send_live_results=True)
        self.mansa_central = Location.objects.create(type=self.type, name="Central Clinic", slug="403012", parent=self.mansa, send_live_results=True)
       
        
        script = """
            cba > join cba 403012 1 cba phiri
            clinic_worker > join clinic 403012 james banda 1111
            hub_worker > join hub 403012 hubman phiri 1111
            district_worker > join dho 403000 dho m. banda 1111            
            district_worker2 > join dho 404000 dho m. kangwa 1111           
            province_worker > join pho 400000 pho mwale 1111
        """
        self.runScript(script)



    def tearDown(self):
        # this call is required if you want to override tearDown
        super(SmsReportsSetUp, self).tearDown()
        try:
            self.clinic.delete()
            self.mansa.delete()
            self.type.delete()
            self.client.logout()
        except:
            pass
            #TODO catch specific exception
    

class TestApp(SmsReportsSetUp):

    def testDhoEidAndBirthReports(self):
        self.assertEqual(0, DhoReportNotification.objects.count())
        Event.objects.create(name="Birth", slug="birth")

        today = date.today()
        month_ago = date(today.year, today.month, 1)-timedelta(days=1)

        script = """
            cba > birth 2 %(last_month)s %(last_month_year)s unicef innovation
            cba > birth 4 %(last_month)s %(last_month_year)s unicef innovation
        """ % {"last_month":month_ago.month, "last_month_year":month_ago.year}
        self.runScript(script)

        self.send_results()

        time.sleep(0.1)

        self.startRouter()
        # even if task is called twice a day only send once
        tasks.send_dho_eid_and_birth_report(self.router)
        time.sleep(0.1)
        tasks.send_dho_eid_and_birth_report(self.router)
        time.sleep(0.1)

        self.stopRouter()
        msgs = self.receiveAllMessages()

        expected_msgs =["""Dho M Banda, %s Mansa District EID & Birth Totals
DBS Samples sent: 3 ***
DBS Results received: 3 ***
Births registered: 2""" % month_ago.strftime("%B"), """Dho M Kangwa, %s Samfya District EID & Birth Totals
DBS Samples sent: 1 ***
DBS Results received: 0 ***
Births registered: 0""" % month_ago.strftime("%B")]

        self.assertEqual(len(msgs), 2)
        for msg in msgs:
            self.assertTrue(msg.text in expected_msgs,"%s not in %s"%(msg.text,"\n".join(msg for msg in expected_msgs)))

    def testPhoEidAndBirthReports(self):
        self.assertEqual(0, DhoReportNotification.objects.count())
        Event.objects.create(name="Birth", slug="birth")
        
        today = date.today()
        month_ago = date(today.year, today.month, 1)-timedelta(days=1)        
        
        script = """
            cba > birth 2 %(last_month)s %(last_month_year)s unicef innovation
            cba > birth 4 %(last_month)s %(last_month_year)s unicef innovation
        """ % {"last_month":month_ago.month, "last_month_year":month_ago.year}
        self.runScript(script)

        self.send_results()

        time.sleep(0.1)

        self.startRouter()
        # even if task is called twice a day only send once
        tasks.send_pho_eid_and_birth_report(self.router)
        time.sleep(0.1)
        tasks.send_pho_eid_and_birth_report(self.router)
        time.sleep(0.1)

        self.stopRouter()
        msgs = self.receiveAllMessages()

        expected_msg ="""Pho Mwale, %s Luapula Province EID & Birth Totals
DBS Samples sent: 4 ***
DBS Results received: 3 ***
Births registered: 2""" % month_ago.strftime("%B")

        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].text, expected_msg)


    def send_results(self):
        today = date.today()
        month_ago = date(today.year, today.month, 1)-timedelta(days=1)

        results = Result.objects.all()
        results.create(requisition_id="0001", clinic=self.mansa_central,
                       result="N",
                       collected_on=month_ago,
                       entered_on=month_ago,
                       notification_status="new")

        results.create(requisition_id="0002", clinic=self.mansa_central,
                       result="P",
                       collected_on=month_ago,
                       entered_on=month_ago,
                       notification_status="new")

        results.create(requisition_id="0003", clinic=self.mibende,
                       result="N",
                       collected_on=month_ago,
                       entered_on=month_ago,
                       notification_status="new")
        results.create(requisition_id="0004", clinic=self.mansa_central,
                       result="N",
                       collected_on=month_ago,
                       entered_on=month_ago,
                       notification_status="new")


        script = """
                clinic_worker > CHECK
                clinic_worker > 1111
            """
        self.runScript(script)
            
            
        # fake that the results were sent last month
        for res in Result.objects.filter(notification_status='sent'):
            res.result_sent_date = month_ago
            res.save()