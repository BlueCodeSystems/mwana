# vim: ai ts=4 sts=4 et sw=4
import logging
from datetime import datetime
from datetime import timedelta
from django.conf import settings
from django.db.models import Q
from mwana import const
from mwana.apps.labresults.messages import TEST_TYPE
from mwana.apps.labresults.models import Result
from mwana.apps.locations.models import Location
from mwana.apps.tlcprinters.models import MessageConfirmation

logger = logging.getLogger(__name__)

verified = Q(lab_results__verified__isnull=True) | \
    Q(lab_results__verified=True)

send_live_results = Q(lab_results__clinic__send_live_results=True)

def clean_up_unconfirmed_results():
    """
    Marks results previously sent to printer but not confirmed as having not
    been sent. Also marks the corresponding confirmation messages as confirmed
    """
    today = datetime.today()
    ago = 1
    if today.weekday() == 0:
        ago = 3
    date_back = datetime(today.year,today.month,today.day) - timedelta(days=ago)

    messages = MessageConfirmation.objects.filter(confirmed=False,
                                                  sent_at__gte=date_back,
                                                  text__icontains=TEST_TYPE)
    for msg in messages:
        req_id = msg.text.split(".")[1].split()[-1]
        
        clinic = msg.connection.contact.location

        try:
            res = Result.objects.get(requisition_id=req_id, clinic=clinic,
                                     result_sent_date__gte=date_back)
            res.notification_status = "new"
            res.save()
            msg.confirmed = True
            msg.save()
        except (Result.DoesNotExist, Result.MultipleObjectsReturned):
            logger.info("Failed to find result for unconfirmed printer message"\
                        " : %s, %s, %s" % (msg.id, req_id, clinic.name))
            continue

def send_results_to_printer(router):
    logger.debug('in tasks.send_results_to_printer')
    if settings.SEND_LIVE_LABRESULTS:
        clean_up_unconfirmed_results()
        new_notified = Q(lab_results__notification_status__in=
                         ['new', 'notified'])

        clinics_with_results = \
            Location.objects.filter(Q(has_independent_printer=True)
                                    & new_notified
                                    & verified & send_live_results).distinct()
        labresults_app = router.get_app(const.LAB_RESULTS_APP)
        for clinic in clinics_with_results:
            logger.info('sending new results to printer at %s' % clinic)
            labresults_app.send_printers_pending_results(clinic)
    else:
        logger.info('not sending new results because '
                    'settings.SEND_LIVE_LABRESULTS is False')
