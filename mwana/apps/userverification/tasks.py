# vim: ai ts=4 sts=4 et sw=4
from datetime import datetime
from datetime import timedelta
import logging

from mwana.apps.userverification.models import UserVerification
from mwana.const import get_clinic_worker_type
from rapidsms.messages import OutgoingMessage
from rapidsms.models import Contact

logger = logging.getLogger(__name__)


VERICATION_MSG = "Hello %s. Are you still working at %s and still using Results160? Please respond with YES or No"

def send_verification_request(router):
    logger.info('sending verification request to clinic workers')

    days_back = 75
    today = datetime.today()
    date_back = datetime(today.year, today.month, today.day) - timedelta(days=days_back)


    supported_contacts = Contact.active.filter(types=get_clinic_worker_type(),
    location__supportedlocation__supported=True).distinct()

    complying_contacts = supported_contacts.filter(message__direction="I", message__date__gte=date_back).distinct()



    defaulting_contacts = set(supported_contacts) - set(complying_contacts)
    counter = 0
    msg_limit = 9

    logger.info('%s clinic workers have not sent messages in the last %s days' % (len(defaulting_contacts), days_back))
    
    
    for contact in defaulting_contacts:
        if UserVerification.objects.filter(contact=contact,
                                           facility=contact.location, request='1',
                                           verification_freq="A",
                                           request_date__gte=date_back).exists():
            continue

        msg = VERICATION_MSG % (contact.name, contact.location.name)

        OutgoingMessage(contact.default_connection, msg).send()

        UserVerification.objects.create(contact=contact,
                                        facility=contact.location, request='1', verification_freq="A")

        counter = counter + 1
        if counter >= msg_limit:
            break