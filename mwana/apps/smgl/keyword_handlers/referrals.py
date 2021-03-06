import logging

from rapidsms.messages import OutgoingMessage
from mwana.apps.smgl.models import Referral, AmbulanceRequest, AmbulanceResponse, Pick, Drop
from mwana.apps.smgl.utils import get_location, to_time, respond_to_session
from mwana.apps.smgl import const
from mwana.apps.contactsplus.models import ContactType
from rapidsms.models import Contact
from datetime import datetime
from mwana.apps.smgl.decorators import registration_required, is_active
from django.template.defaultfilters import yesno

from mwana.apps.smgl.app import (get_value_from_form, send_msg, ER_TO_DRIVER,
                                 ER_TO_TRIAGE_NURSE, ER_STATUS_UPDATE,
                                 INITIAL_AMBULANCE_RESPONSE, _get_allowed_ambulance_workflow_contact,
                                 NOT_REGISTERED_TO_CONFIRM_ER, ER_CONFIRM_SESS_NOT_FOUND, ER_TO_CLINIC_WORKER,
                                 AMB_OUTCOME_FILED, FACILITY_NOT_RECOGNIZED,
                                 AMB_RESPONSE_ORIGINATING_LOCATION_INFO, AMB_RESPONSE_NOT_AVAILABLE,
                                 AMB_RESPONSE_ALREADY_HANDLED)

logger = logging.getLogger(__name__)
# In RapidSMS, message translation is done in OutgoingMessage, so no need
# to attempt the real translation here.  Use _ so that makemessages finds
# our text.
_ = lambda s: s


@registration_required
@is_active
def refer(session, xform, router):
    """
    Handler for REF keyword

    Used to record a referral to another location

    Format:
    REFER Mother_UID receiving_facility_id Reason TIME EM/NEM

    A CBA is allowed and expected to only submit the Mother_UID
    such as: REFER Mother_UID
    """

    assert session.connection.contact is not None, \
        "Must be a registered contact to refer"
    assert session.connection.contact.location is not None, \
        "Contact must have a location to refer"
    contact = session.connection.contact
    name = contact.name
    mother_id = xform.xpath("form/unique_id")

    referring_loc = session.connection.contact.location


    # IF CBA, DO NOT SEND AN EMERGENCY REQUEST, JUST NOTIFY via
    # _get_people_to_notify
    is_cba = ['cba'] == list(
        contact.types.all().values_list('slug', flat=True))
    if is_cba:
        # If a CBA referred the mother, we have to take it that the parent facility is the
        # destination or referral facility.
        parent_facility = referring_loc.parent or referring_loc
        referral = Referral(facility=parent_facility, form_id=xform.get_id,
                            session=session, date=datetime.utcnow())
        referral.set_mother(mother_id)
        referral.from_facility = referring_loc
        referral.save()

        from_facility = referring_loc.name if not referring_loc.parent else "%s (in %s)" % \
            (referring_loc.name, referring_loc.parent.name)
        for con in _get_people_to_notify(referral):
            if con.default_connection:
                if const.CTYPE_DATACLERK in con.types.values_list('slug', flat=True):
                    # Data Clerks and facility in charges should not be asked
                    # to send the resp
                    msg = const.REFERRAL_CBA_NOTIFICATION_WITHOUT_REQUEST % {
                        "unique_id": mother_id,
                        "village": referring_loc.name,
                        "phone": session.connection.identity,
                        "name": contact.name
                    }
                else:
                    msg = const.REFERRAL_CBA_NOTIFICATION % {
                        "unique_id": mother_id,
                        "village": referring_loc.name,
                        "phone": session.connection.identity,
                        "name":contact.name
                    }
                router.outgoing(OutgoingMessage(con.default_connection, msg))
        # respond to the sending CBA
        cba_thanks = const.REFERRAL_CBA_THANKS % {
            "name": name,
            "facility_name": parent_facility.name
        }
        # Schedule a reminder in 20 minutes if there is no response
        return respond_to_session(router, session, cba_thanks)
    else:
        # if the sender wasn't a CBA we expect referral facility, reasons,
        # status
        facility_id = xform.xpath("form/facility")
        MISSING_FACILITY = "Kindly enter the facility on your REFER message concerning mother ID: %(unique_id)s" % {
            "unique_id": mother_id}
        if not facility_id:
            return respond_to_session(router,
                                      session,
                                      MISSING_FACILITY,
                                      is_error=True,
                                      **{"facility": facility_id}
                                      )
        loc = get_location(facility_id)
        if not loc:
            return respond_to_session(router, session, FACILITY_NOT_RECOGNIZED,
                                      is_error=True,
                                      **{"facility": facility_id})

        referral = Referral(facility=loc, form_id=xform.get_id,
                            session=session, date=datetime.utcnow())
        referral.set_mother(mother_id)
        reasons = xform.xpath("form/reason")
        if reasons:
            for r in reasons.split(" "):
                referral.set_reason(r)
        referral.from_facility = session.connection.contact.location
        try:
            referral.time = to_time(xform.xpath("form/time"))
        except ValueError, e:
            return respond_to_session(router, session, str(e),
                                      is_error=True)
        status = xform.xpath("form/status")
        referral.status = status
        referral.save()
        # Get all the referrals whose destination facility is the same as the new ORIGIN facility. Ensures we only get the past
        # referrals sent to the location being referred from.
        for past_referral in Referral.objects.filter(facility=referral.from_facility, mother_uid=mother_id):
            past_referral.re_referral = referral
            past_referral.save()
        # If not an emergency, we just notify via get people to notify.
        """
        if status == "nem":
            for con in _get_people_to_notify(referral):
                verbose_reasons = [Referral.REFERRAL_REASONS[r] for r in referral.get_reasons()]
                msg = const.REFERRAL_NOTIFICATION %{
                                                    "unique_id":referral.mother_uid,
                                                    "facility":referral.from_facility,
                                                    "reason":", ".join(verbose_reasons),
                                                    "time": referral.time.strftime("%H:%M"),
                                                    "is_emergency": yesno(referral.is_emergency),
                                                    }
                router.outgoing(OutgoingMessage(con.default_connection, msg))

            referral_response = const.REFERRAL_RESPONSE % {"name": name,
                                              "unique_id": referral.mother_uid,
                                              "facility_name":referral.facility.name}
            return respond_to_session(router, session, referral_response)
        """
        # Generate an Ambulance Request
        session.template_vars.update(
            {"sender_phone_number": session.connection.identity})
        amb = AmbulanceRequest()
        amb.session = session
        session.template_vars.update(
            {"from_location": str(referral.from_facility.name)})
        amb.set_mother(mother_id)
        amb.save()
        referral.amb_req = amb
        referral.save()

        if is_from_facility(session.connection.contact):
            # If this is a referral from a facility, we request the people at
            # the RECEIVING facility/ Hospital

            msg = const.REFERRAL_FACILITY_TO_HOSPITAL_NOTIFICATION % {
                'unique_id': mother_id,
                'phone': session.connection.identity,
                'facility_name': referral.from_facility.name,
                'reason': " ".join(reason for reason in referral.get_reasons()),
                "title": ",".join([contact_type.name for contact_type in contact.types.all()]),
                "name":contact.name
                }
            _broadcast_to_ER_users(
                amb, session, xform, facility=referral.facility, router=router, message=msg)
            #Tell the data clerks that we are on it.
            data_clerks_workers_and_incharges = Contact.objects.filter(
                types__slug__in=[const.CTYPE_DATACLERK, const.CTYPE_INCHARGE, const.CTYPE_CLINICWORKER],
                location=referral.facility,
                is_active=True)

            for con in data_clerks_workers_and_incharges:
                if con != contact:
                    if const.CTYPE_DATACLERK in con.types.values_list('slug', flat=True):
                        #For the data clerk, no need to respond
                        send_msg(con.default_connection, const.REFERRAL_FACILITY_TO_HOSPITAL_NOTIFICATION_WITHOUT_REQUEST%{
                            'unique_id': mother_id,
                            'phone': session.connection.identity,
                            'reason': " ".join(reason for reason in referral.get_reasons()),
                            "facility_name": referral.from_facility,
                            "name":contact.name,
                            "title": ",".join([contact_type.name for contact_type in contact.types.all()]),
                            }, router)

                    else:
                        send_msg(con.default_connection, const.REFERRAL_FACILITY_TO_HOSPITAL_NOTIFICATION%{
                            'unique_id': mother_id,
                            'phone': session.connection.identity,
                            'reason': " ".join(reason for reason in referral.get_reasons()),
                            "facility_name": referral.from_facility,
                            "name":contact.name,
                            "title": ",".join([contact_type.name for contact_type in contact.types.all()]),
                            }, router)

            # Respond that we're on it.
            referral_response = const.REFERRAL_RESPONSE % {
                "name": name,
                "unique_id": mother_id,
                "facility_name": referral.facility.name
            }
            return respond_to_session(router, session, referral_response)

        elif is_from_hospital(session.connection.contact):
            # If this case we request the ambulance driver + Triage Nurse at
            # the VERY facility
            message = const.REFERRAL_TO_HOSPITAL_DRIVER % {
                "referral_facility": referral.facility,
                "referring_facility": referral.from_facility}

            driver_amb_msg = const.DRIVER_NOTIFICATION% {
                'unique_id': mother_id,
                'phone': session.connection.identity,
                'facility_name': referral.from_facility.name,
                'reason': " ".join(reason for reason in referral.get_reasons())}


            #Send to the drivers and other ER users without sending to the person who sent it in.
            _broadcast_to_ER_users(
                amb, session, xform, facility=referral.from_facility, router=router, message=driver_amb_msg, excluded=[contact])
            # notify triage nurse at receiving facility
            for con in _get_people_to_notify_hospital(referral):
                if const.CTYPE_DATACLERK in con.types.values_list('slug', flat=True):
                    msg = const.REFERRAL_TO_DESTINATION_HOSPITAL_WITHOUT_REQUEST % {
                        "unique_id": referral.mother_uid,
                        'reason': " ".join(reason for reason in referral.get_reasons()),
                        'facility_name':referral.from_facility,
                        'name':contact.name,
                        'phone':contact.default_connection.identity,
                        "title": ",".join([contact_type.name for contact_type in contact.types.all()]),
                    }
                else:
                    msg = const.REFERRAL_TO_DESTINATION_HOSPITAL % {
                        "unique_id": referral.mother_uid,
                        'reason': " ".join(reason for reason in referral.get_reasons()),
                        'facility_name':referral.from_facility,
                        'name':contact.name,
                        'phone':contact.default_connection.identity,
                        "title": ",".join([contact_type.name for contact_type in contact.types.all()]),
                    }
                router.outgoing(OutgoingMessage(con.default_connection, msg))
            #Tell the data clerks that we are on it.
            data_clerks_workers_and_incharges = Contact.objects.filter(
                types__slug__in=[const.CTYPE_DATACLERK, const.CTYPE_INCHARGE, const.CTYPE_CLINICWORKER],
                location=referral.from_facility,
                is_active=True)

            for con in data_clerks_workers_and_incharges:
                if con != contact:

                    send_msg(con.default_connection, const.REFERRAL_NOTIFICATION_OTHER_USERS%{
                        'unique_id': mother_id,
                        'phone': session.connection.identity,
                        "origin_facility": referral.from_facility,
                        "dest_facility": referral.facility,
                        "name":contact.name,
                        "title": ",".join([contact_type.name for contact_type in contact.types.all()]),
                        }, router)


            # respond to the sender
            referral_response = const.REFERRAL_RESPONSE % {"name": name,
                                                           "unique_id": referral.mother_uid,
                                                           "facility_name": referral.facility.name}
            return respond_to_session(router, session, referral_response)


@registration_required
@is_active
def referral_outcome(session, xform, router):
    contact = session.connection.contact
    name = contact.name
    mother_id = xform.xpath("form/unique_id")
    refs = Referral.objects.filter(
        mother_uid=mother_id.lower()).order_by('-date')
    if not refs.count():
        return respond_to_session(router, session, const.REFERRAL_NOT_FOUND,
                                  is_error=True, **{"unique_id": mother_id})

    ref = refs[0]
    if ref.responded:
        return respond_to_session(
            router, session, const.REFERRAL_ALREADY_RESPONDED,
            is_error=True, **{"unique_id": mother_id})
    ref.responded = True

    if xform.xpath("form/mother_outcome").lower() == const.REFERRAL_OUTCOME_NOSHOW:
        ref.mother_showed = False
    else:
        ref.mother_showed = True
        ref.mother_outcome = xform.xpath("form/mother_outcome")
        ref.baby_outcome = xform.xpath("form/baby_outcome")
        ref.mode_of_delivery = xform.xpath("form/mode_of_delivery")

    if xform.xpath("form/delivery_on_the_way"):
        ref.delivered_on_the_way = True
    ref.save()

    #We need the ambulance request so that we can notify the mother as well
    ambulance_requests = AmbulanceRequest.objects.filter(mother_uid=mother_id)\
        .order_by('-id')
    try:
        ambulance_request = ambulance_requests[0]
    except IndexError:
        ambulance_request = None
    """
    if ref.amb_req:
        responses = ref.amb_req.ambulanceresponse_set
        if responses.count() == 0 or responses.all().order_by('-responded_on')[0].response != 'na':
            session.template_vars.update({"contact_type": contact.types.all()[0],
                                          "name": contact.name})
            if xform.xpath("form/mode_of_delivery") == 'noamb':
                _broadcast_to_ER_users(ref.amb_req, session, xform,
                                       message=_(AMB_OUTCOME_FILED), router=router)
            return respond_to_session(router, session, const.AMB_OUTCOME_ORIGINATING_LOCATION_INFO,
                                      unique_id=session.template_vars['unique_id'],
                                      outcome=ref.mother_outcome)
    """

    if ref.mother_showed:
        notification_origin = const.REFERRAL_OUTCOME_NOTIFICATION % \
            {"unique_id": ref.mother_uid,
             "date": ref.date.strftime('%d %b %Y'),
             "mother_outcome": ref.get_mother_outcome_display(),
             "baby_outcome": ref.get_baby_outcome_display(),
             "delivery_mode": ref.get_mode_of_delivery_display()}

        if ref.delivered_on_the_way:
            notification_origin = const.REFERRAL_OUTCOME_NOTIFICATION_OTW %\
             {"unique_id": ref.mother_uid,
             "date": ref.date.strftime('%d %b %Y'),
             "mother_outcome": ref.get_mother_outcome_display(),
             "baby_outcome": ref.get_baby_outcome_display(),
             "delivery_mode": ref.get_mode_of_delivery_display()}

        #notification for users from same location as sender so the destination
        notification_dest = const.REFERRAL_OUTCOME_NOTIFICATION_DEST %\
            {
                "unique_id":ref.mother_uid,
                "name": contact.name,
                "date":ref.date.strftime('%d %b %Y'),
                "origin":ref.from_facility
            }
    else:
        notification_origin = notification_dest = const.REFERAL_OUTCOME_NOTIFICATION_NOSHOW %{
            "unique_id": ref.mother_uid,
            "date": ref.date.strftime('%d %b %Y')}
    if is_from_hospital(ref.session.connection.contact):
        # Let everyone at the referral hospital know
        for con in _get_people_to_notify_hospital(ref):
            # do not send to originator
            if con != contact:
                send_msg(con.default_connection, notification_dest, router)
        # notify people at origin
        for con in _get_people_to_notify_outcome(ref):
            send_msg(con.default_connection, notification_origin, router)

    else:
        # Let everyone know that it has been handled
        for con in _get_people_to_notify(ref):
            # do not send to originator
            if con != contact:
                send_msg(con.default_connection, notification_dest, router)
        # notify people at origin
        for con in _get_people_to_notify_outcome(ref):
            send_msg(con.default_connection, notification_origin, router)
    """
    if ambulance_request:
        #Tell the ambulance driver of the outcome
        send_msg(ambulance_request.ambulance_driver.default_connection,
            notification_origin, router)
    """
    return respond_to_session(
        router, session, const.REFERRAL_OUTCOME_RESPONSE,
        **{'name': name, "unique_id": mother_id})


@registration_required
@is_active
def emergency_response(session, xform, router):
    """
    This handler deals with a status update from an ER Driver or Triage Nurse
    about a specific ambulance

    i.e. Ambulance on the way/delayed/not available
    """
    logger.debug('POST PROCESSING FOR RESP KEYWORD')
    contact = _get_allowed_ambulance_workflow_contact(session)
    if not contact:
        return respond_to_session(
            router, session, NOT_REGISTERED_TO_CONFIRM_ER,
            is_error=True)

    unique_id = get_value_from_form('unique_id', xform)
    try:
        ref = Referral.objects.filter(mother_uid=unique_id).latest('date')
    except Referral.DoesNotExist:
        return respond_to_session(router, session, ER_CONFIRM_SESS_NOT_FOUND,
                                  is_error=True, **{'unique_id': unique_id})
    # Save the fact that we now have a response
    ref.has_response = True
    ref.save()
    if cba_initiated(ref.session.connection.contact):
        # Tell the initiating CBA that a RESP has been received.
        resp_update  = const.RESP_CBA_UPDATE%{
            "phone": contact.default_connection.identity,
            "responder_name": contact.name
        }
        send_msg(ref.session.connection,
                 resp_update,
                 router, **session.template_vars)

        # Let everyone know that this resp has been handled
        other_users_message = const.REFERRAL_RESPONSE_NOTIFICATION_OTHER_USERS%{
            "user_type":",".join(contacttype.name for contacttype in contact.types.all()),
            "name": contact.name,
            "unique_id": unique_id
        }
        for con in _get_people_to_notify(ref):
            if con != contact:  # Let's not tell the sender.
                send_msg(con.default_connection,
                         other_users_message,
                         router)
        # Confirm to the sender that we have seen their response.
        thank_message = const.RESP_THANKS % {
            "unique_id": unique_id,
            "name": contact.name
        }
        #thanks the sender
        return respond_to_session(router, session, thank_message)
    else:
        ambulance_response = AmbulanceResponse()
        ambulance_response.responder = contact
        ambulance_response.session = session

        ambulance_response.mother_uid = unique_id
        session.template_vars.update({'unique_id': unique_id})
        # try match uid to mother
        ambulance_response.set_mother(unique_id)
        ambulance_response.mother_uid = unique_id

        status = get_value_from_form('status', xform).lower()
        ambulance_response.response = status
        session.template_vars.update({"status": status.upper(),
                                      "response": status.upper()})

        # we might be dealing with a mother that has gone through ER multiple
        # times
        ambulance_requests = AmbulanceRequest.objects.filter(mother_uid=unique_id)\
            .exclude(referral__responded=True)\
            .order_by('-id')

        if not ambulance_requests.count():
            # session doesn't exist or it has already been confirmed
            return respond_to_session(
                router, session, ER_CONFIRM_SESS_NOT_FOUND,
                is_error=True, **{'unique_id': unique_id})

        ambulance_response.ambulance_request = ambulance_request = ambulance_requests[
            0]

        confirm_contact_type = contact.types.all()[0]
        session.template_vars.update({"confirm_type": confirm_contact_type,
                                      "name": contact.name})

        ambulance_request.save()
        ambulance_response.save()
        if status == 'na':
            session.template_vars.update(
                {"sender_phone_number": ref.session.connection.identity,
                 "from_location": str(ref.from_facility.name)})
            try:
                help_admins = _pick_help_admin(session, xform, ref.facility)
            except Exception:
                logger.error(
                    'No Help Admin found (or missing connection for Ambulance Session: %s, XForm Session: %s, XForm: %s' % (ambulance_response, session, xform))
            else:
                for ha in help_admins:
                    send_msg(
                        ha.default_connection, AMB_RESPONSE_NOT_AVAILABLE, router, **session.template_vars)
                return True

        if is_driver(contact):
            #Set the ambulance driver to the once who just responded
            ambulance_request.ambulance_driver = contact
            ambulance_request.save()
            if not status:
                # if the sender is  a driver, ensure that they haven't left out
                # status
                RESP_MISSING_STATUS = "Your message is missing the STATUS"
                return respond_to_session(router, session, RESP_MISSING_STATUS, is_error=True, **{'unique_id': unique_id})
            else:
                facility_to_hosp = const.AMB_RESP_STATUS % {
                    "unique_id": unique_id,
                    "status": ambulance_response.get_response_display(),
                    "phone": session.connection.identity,
                    "facility": ref.from_facility,
                    "name":contact.name
                    }

                hosp_to_hosp = const.AMB_RESP_STATUS_HOSP % {
                    "unique_id": unique_id,
                    "status": ambulance_response.get_response_display(),
                    "phone": session.connection.identity,
                    "facility": ref.from_facility,
                    "name":contact.name
                }

                thank_message = const.RESP_THANKS_AMB_DRIVER % {
                    "unique_id": unique_id,
                    "name": contact.name,
                }
                if is_from_hospital(ref.session.connection.contact):
                    # Let everyone know that it has been handled
                    for con in _get_people_to_notify_hospital(ref):
                        # do not send to responder
                        if con != contact:
                            send_msg(con.default_connection, hosp_to_hosp, router)

                    # notify people at origin
                    for con in _get_people_to_notify_response(ref):
                        if con != contact:
                            send_msg(con.default_connection, hosp_to_hosp, router)
                else:
                    # Notify people at destination
                    for con in _get_people_to_notify(ref):
                        if con != contact:
                            send_msg(con.default_connection, facility_to_hosp, router)
                    # notify people at origin
                    for con in _get_people_to_notify_response(ref):
                        send_msg(con.default_connection, facility_to_hosp, router)

                ref_type = referral_type(ref)
                if ref_type == 'facility_to_hospital':
                    #Tell the driver at the destination
                    driver_notification = const.REFERRAL_RESPONSE_NOTIFICATION_OTHER_USERS_STATUS %{
                        "unique_id":unique_id,
                        "user_type":",".join(contacttype.name for contacttype in contact.types.all()),
                        "name":contact.name,
                        "status":ambulance_response.get_response_display(),
                        "facility":ref.from_facility}
                    for con in _pick_er_drivers(ref.facility):
                        if con != contact:
                            send_msg(con.default_connection, driver_notification, router)
                elif ref_type == 'hospital_to_hospital':
                    #When referral is from hospital to hospital we need the
                    #drivers at the origin facility
                    driver_notification = const.REFERRAL_RESPONSE_NOTIFICATION_OTHER_USERS_STATUS_HOSP_TO_HOSP %{
                        "unique_id":unique_id,
                        "user_type":",".join(contacttype.name for contacttype in contact.types.all()),
                        "name":contact.name,
                        "status":ambulance_response.get_response_display(),
                        "facility":ref.from_facility}
                    for con in _pick_er_drivers(ref.from_facility):
                        if con != contact:
                            send_msg(con.default_connection, driver_notification, router)
                #thanks the sender
                return respond_to_session(router, session, thank_message)
        else:
            origin_notification = driver_notification =  const.REF_TRIAGE_NURSE_RESP_NOTIF_ORIGIN %{
                "unique_id":unique_id,
                "phone":session.connection.identity,
                "facility":ref.facility,
                "title": ",".join([contact_type.name for contact_type in contact.types.all()]),
                "name": contact.name
            }

            dest_notification = const.REF_TRIAGE_NURSE_RESP_NOTIF % {
                "unique_id": unique_id,
                "phone": session.connection.identity,
                "title": ",".join([contact_type.name for contact_type in contact.types.all()]),
                "name": contact.name
            }

            if status:
                #If the sender added the status
                origin_notification =  const.REF_TRIAGE_NURSE_RESP_NOTIF_ORIGIN_STATUS %{
                    "unique_id": unique_id,
                    "phone": session.connection.identity,
                    "name":contact.name,
                    "title": ",".join([contact_type.name for contact_type in contact.types.all()]),
                    "status": ambulance_response.get_response_display()
                }

                driver_notification = const.TRIAGE_RESP_NOTIF_STATUS%{
                    "unique_id": unique_id,
                    "status": ambulance_response.get_response_display(),
                    "phone": session.connection.identity,
                    "title": ",".join([contact_type.name for contact_type in contact.types.all()]),
                    "name": contact.name
                }

            thank_message = const.RESP_THANKS % {
                "unique_id": unique_id,
                "name": contact.name
            }
            # Let everyone know that it has been handled
            if is_from_hospital(ref.session.connection.contact):
                # Let everyone know that it has been handled
                for con in _get_people_to_notify_hospital(ref):
                    # do not send to originator
                    if con != contact:
                        send_msg(con.default_connection, dest_notification, router)

                # notify people at origin
                for con in _get_people_to_notify_response(ref):
                    send_msg(con.default_connection, origin_notification, router)

            else:
                if not status:
                    RESP_MISSING_STATUS = "Your message is missing the STATUS"
                    return respond_to_session(router, session, RESP_MISSING_STATUS, is_error=True, **{'unique_id': unique_id})

                # Let everyone know that it has been handled
                for con in _get_people_to_notify(ref):
                    # do not send to originator
                    if con != contact:
                        send_msg(con.default_connection, dest_notification, router)
                # notify people at origin
                for con in _get_people_to_notify_response(ref):
                    send_msg(con.default_connection, origin_notification, router)


            ref_type = referral_type(ref)
            if ref_type == 'facility_to_hospital':
                #Tell the driver at the destination facility
                for con in _pick_er_drivers(ref.facility):
                    send_msg(con.default_connection, driver_notification, router)
            elif ref_type == 'hospital_to_hospital':
                #When referral is from hospital to hospital we need the
                #drivers at the origin facility
                for con in _pick_er_drivers(ref.from_facility):
                    send_msg(con.default_connection, driver_notification, router)

            #thanks the sender
            return respond_to_session(router, session, thank_message)

        """
    else:
        clinic_recip = _pick_clinic_recip(session, xform, ref.facility)
        if clinic_recip:
            if clinic_recip.default_connection:
                send_msg(clinic_recip.default_connection, ER_TO_CLINIC_WORKER, router, **session.template_vars)
            else:
                logger.error('No Receiving Clinic Worker found (or missing connection for Ambulance Session: %s, XForm Session: %s, XForm: %s' % (ambulance_response, session, xform))
        """
    return True


def pick(session, xform, router):
    logger.debug('POST PROCESSING FOR PICK KEYWORD')
    contact = _get_allowed_ambulance_workflow_contact(session)
    if not contact:
        return respond_to_session(
            router, session, NOT_REGISTERED_TO_CONFIRM_ER,
            is_error=True)
    mother_id = xform.xpath("form/unique_id")
    # Find the referral
    try:
        referral = Referral.objects.filter(mother_uid=mother_id).order_by('-date')[0]
    except IndexError:
        return respond_to_session(
            router,
            session,
            "A referral was not found for mother ID %(unique_id)s" % {
                "unique_id": mother_id},
            is_error=True)

    if referral.pick:
        return respond_to_session(
            router,
            session,
            "This mother with ID: %(unique_id)s has already been picked by %(picker_name)s. Call them on: %(picker_num)s." % {
                "unique_id": mother_id,
                "picker_name": referral.pick.session.connection.contact.name,
                "picker_num": referral.pick.session.connection.identity},
            is_error=True)

    referral.pick = Pick.objects.create(session=session)
    referral.save()

    pick_thanks = const.PICK_THANKS % {
        "unique_id": mother_id}

    pick_notification = const.PICK_NOTIFICATION % {
        "unique_id":mother_id,
        "name":session.connection.contact.name,
        "phone":session.connection.identity,
        "facility":referral.from_facility,
        "title": ",".join([contact_type.name for contact_type in contact.types.all()])
    }

    #Notify others
    #Notify those at origin
    if is_from_hospital(referral.session.connection.contact):
        # Let everyone know that it has been handled
        for con in _get_people_to_notify_hospital(referral):
            # do not send to responder
            if con != contact:
                send_msg(con.default_connection, pick_notification, router)
        """
        # notify people at origin
        for con in _get_people_to_notify_response(referral):
            if con != contact:
                send_msg(con.default_connection, pick_notification, router)
        """
    else:
        # Notify people at destination
        for con in _get_people_to_notify(referral):
            if con != contact:
                send_msg(con.default_connection, pick_notification, router)
        """
        # notify people at origin
        for con in _get_people_to_notify_response(referral):
            send_msg(con.default_connection, pick_notification, router)
        """
    return respond_to_session(router, session, pick_thanks)


def drop(session, xform, router):
    logger.debug('POST PROCESSING FOR DROP KEYWORD')
    contact = _get_allowed_ambulance_workflow_contact(session)
    if not contact:
        return respond_to_session(
            router, session, NOT_REGISTERED_TO_CONFIRM_ER,
            is_error=True)
    mother_id = xform.xpath("form/unique_id")
    # Find the referral
    try:
        referral = Referral.objects.filter(mother_uid=mother_id).order_by('-date')[0]
    except IndexError:
        return respond_to_session(
            router,
            session,
            "A referral was not found for mother ID %(unique_id)s" % {
                "unique_id": mother_id},
            is_error=True)
    if referral.drop:
        return respond_to_session(
            router,
            session,
            "This mother with ID: %(unique_id)s has already been dropped by %(droper_name)s. Call them on: %(droper_num)s." % {
                "unique_id": mother_id,
                "droper_name": referral.drop.session.connection.contact.name,
                "droper_num": referral.drop.session.connection.identity},
            is_error=True)

    referral.drop = Drop.objects.create(session=session)
    referral.save()
    drop_thanks = const.DROP_THANKS % {
        "unique_id": mother_id}

    drop_notification = const.DROP_NOTIFICATION %{
        "unique_id":mother_id,
        "name":session.connection.contact.name,
        "phone":session.connection.identity,
        "facility":referral.facility,
        "title": ",".join([contact_type.name for contact_type in contact.types.all()])
    }

    #If the person who sent the referral in is from a hospital, we need to handle
    #the notifications differently
    if is_from_hospital(referral.session.connection.contact):
        # Let everyone know that it has been handled
        """
        for con in _get_people_to_notify_hospital(referral):
            # do not send to responder
            if con != contact:
                send_msg(con.default_connection, drop_notification, router)
        """

        # notify people at origin
        for con in _get_people_to_notify_response(referral):
            if con != contact:
                send_msg(con.default_connection, drop_notification, router)
    else:
        # Notify people at destination
        """
        for con in _get_people_to_notify(referral):
            if con != contact:
                send_msg(con.default_connection, drop_notification, router)
        """
        # notify people at origin
        for con in _get_people_to_notify_response(referral):
            send_msg(con.default_connection, drop_notification, router)

    return respond_to_session(router, session, drop_thanks, **{'unique_id': mother_id})


def _get_people_to_notify_hospital(referral):
    # This method will specifically be used to get the people to notify
    # for Hospital to Hospital referrals
    # So basically people at the receiving facility.
    types = ContactType.objects.filter(
        slug__in=[const.CTYPE_TRIAGENURSE, const.CTYPE_INCHARGE,
                  const.CTYPE_CLINICWORKER, const.CTYPE_DATACLERK
            ]
    ).all()
    return Contact.objects.filter(types__in=types,
                                  location=referral.facility,
                                  is_active=True)


def _get_people_to_notify(referral, excluded=None):
    # who to notifiy on an initial referral
    # this should be the people who are being referred to
    types = ContactType.objects.filter(
        slug__in=[const.CTYPE_DATACLERK, const.CTYPE_TRIAGENURSE,
                  const.CTYPE_CLINICWORKER, const.CTYPE_INCHARGE]
    ).all()

    if excluded:
        types = types.exclude(slug__in=excluded)

    loc_parent = referral.from_facility.parent if referral.from_facility else None
    facility_lookup = referral.facility or loc_parent
    return Contact.objects.filter(types__in=types,
                                  location=facility_lookup,
                                  is_active=True)


def _get_people_to_notify_response(referral):
    # who to notify on response
    types = ContactType.objects.filter(
        slug__in=[const.CTYPE_DATACLERK,
                  const.CTYPE_TRIAGENURSE, const.CTYPE_CLINICWORKER, const.CTYPE_INCHARGE]
    ).all()
    loc_parent = referral.from_facility
    facility_lookup = loc_parent
    return Contact.objects.filter(types__in=types,
                                  location=facility_lookup,
                                  is_active=True)


def _get_people_to_notify_outcome(referral):
    # who to notifiy when we've collected a referral outcome
    # this should be the people who made the referral
    # (more specifically, the person who sent it in + all
    # data clerks, Health workers and in-charges at their facility)
    types = ContactType.objects.filter(
        slug__in=[
            const.CTYPE_TRIAGENURSE, const.CTYPE_DATACLERK, const.CTYPE_INCHARGE, const.CTYPE_CLINICWORKER]
    ).all()
    from_facility = referral.from_facility
    facility_contacts = list(Contact.objects.filter(
        types__in=types,
        location=from_facility,
        is_active=True)
    ) if from_facility else []
    contact = referral.get_connection().contact
    if contact and contact.is_active and contact not in facility_contacts:
        facility_contacts.append(referral.get_connection().contact)

    other_contacts = []
    if referral.past_referrals:
        for past_referral in referral.past_referrals.all():
            past_contacts = _get_people_to_notify_outcome(past_referral)
            other_contacts.extend(past_contacts)
    return list(set(facility_contacts + other_contacts))


def _pick_er_drivers(facility):
    ad_type = ContactType.objects.get(slug__iexact='am')
    ads = Contact.objects.filter(types=ad_type,
                                 location=facility,
                                 is_active=True)
    if ads.count():
        return ads
    else:
        raise Exception('No Ambulance Driver type found!')

def referral_type(referral):
    """should return either facility_to_hospital or hospital_to_hospital or cba_to_facility
    """
    contact = referral.session.connection.contact
    if cba_initiated(contact):
        return "cba_to_facility"
    elif is_from_facility(contact):
        return "facility_to_hospital"
    elif is_from_hospital(contact):
        return "hospital_to_hospital"

def _pick_er_triage_nurse(facility):
    tn_type = ContactType.objects.get(slug__iexact='tn')
    tns = Contact.objects.filter(types=tn_type,
                                 location=facility,
                                 is_active=True)
    if tns.count():
        return tns[0]
    else:
        raise Exception('No Triage Nurse type found!')


def _pick_clinic_recip(session, xform, receiving_facility):
    cw_type = ContactType.objects.get(slug__iexact=const.CTYPE_CLINICWORKER)
    cws = Contact.objects.filter(types=cw_type,
                                 location=receiving_facility,
                                 is_active=True)
    if cws.count():
        return cws[0]
    else:
        logger.error('No clinic worker found!')


def _pick_help_admin(session, xform, receiving_facility):
    help_admins = Contact.objects.filter(is_help_admin=True,
                                         location=receiving_facility,
                                         is_active=True)
    if help_admins.count():
        return help_admins
    else:
        raise Exception('No Help Admin type found!')


def _broadcast_to_ER_users(ambulance_session, session, xform, router, facility=None, message=None, excluded=[]):
    """
    Broadcasts a message to the Emergency Response users.  If message is not
    specified, will send the default initial ER message to each respondent.

    exclude perimeter is used to exclude some users from receiving broadcasts
    """
    if not facility:
        ref = ambulance_session.referral_set.all()[0]
        facility = ref.facility
    try:
        ambulance_drivers = _pick_er_drivers(facility)
    except Exception:
        logger.error(
            'No Ambulance Driver found (or missing connection) for Ambulance Session: %s, XForm Session: %s, XForm: %s' %
            (ambulance_session, session, xform))
    else:
        for ambulance_driver in ambulance_drivers:
            # This is just for now before we can properly handle multiple
            # drivers.
            #ambulance_session.ambulance_driver = ambulance_driver
            if ambulance_driver.default_connection and ambulance_driver not in excluded:
                if message:
                    send_msg(ambulance_driver.default_connection,
                             message, router, **session.template_vars)
                else:
                    send_msg(ambulance_driver.default_connection,
                             ER_TO_DRIVER, router, **session.template_vars)

    try:
        tn = _pick_er_triage_nurse(facility)
    except Exception:
        logger.error(
            'No Triage Nurse found (or missing connection) for Ambulance Session: %s, XForm Session: %s, XForm: %s' %
            (ambulance_session, session, xform))
    else:
        ambulance_session.triage_nurse = tn
        if tn.default_connection and tn not in excluded:
            if message:
                send_msg(
                    tn.default_connection, message, router, **session.template_vars)
            else:
                send_msg(
                    tn.default_connection, ER_TO_TRIAGE_NURSE, router, **session.template_vars)

    ambulance_session.save()

    return



def _broadcast_Notification_to_ER_users(ambulance_session, session, xform, router, message=None):
    """Broadcasts the message to the Response users, usually these are just the people at the referring facility
    """
    ref = ambulance_session.referral_set.all()[0]
    facility = ref.from_facility

    try:
        tn = _pick_er_triage_nurse(facility)
    except Exception:
        logger.error(
            'No Triage Nurse found (or missing connection) for Ambulance Session: %s, XForm Session: %s, XForm: %s' %
            (ambulance_session, session, xform))
    else:
        ambulance_session.triage_nurse = tn
        if tn.default_connection:
            if message:
                send_msg(
                    tn.default_connection, message, router, **session.template_vars)
            else:
                send_msg(
                    tn.default_connection, ER_TO_TRIAGE_NURSE, router, **session.template_vars)
    ambulance_session.save()


def _get_location_type(contact):
    # The locationType slug of the contacts location
    return contact.location.type.slug


def cba_initiated(contact):
    return ['cba'] == list(contact.types.all().values_list('slug', flat=True))


def is_driver(contact):
    return ['am'] == list(contact.types.all().values_list('slug', flat=True))


def is_from_facility(contact):
    facilities = ['rural_health_centre']
    loc = _get_location_type(contact)
    if loc and loc in facilities:
        return True
    else:
        return False


def is_from_hospital(contact):
    #is the person from a hospital
    hospitals = ['urban_health_centre']
    loc = _get_location_type(contact)
    if loc and loc in hospitals:
        return True
    else:
        return False
