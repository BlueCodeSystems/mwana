from mwana.settings_project import *

# Malawi:
RESULTS160_SLUGS = {
    'CBA_SLUG': 'cba',
    'PATIENT_SLUG': 'patient',
    'CLINIC_WORKER_SLUG': 'clinic-worker',
    'DISTRICT_WORKER_SLUG': 'district-worker',
    # location types:
    'CLINIC_SLUGS': ('clinic', 'health_centre', 'hospital', 'maternity',
                     'dispensary', 'rural_hospital', 'mental_hospital',
                     'district_hospital', 'central_hospital',
                     'voluntary_counselling', 'rehabilitation_centre'),
    'ZONE_SLUGS': ('zone',),
    'DISTRICT_SLUGS': ('district',),
}

RESULTS160_SCHEDULES = {
    # send out results almost immediately (for demo purposes only)
    'send_results_notification': {'minutes': '*'},
}

TIME_ZONE = 'Africa/Blantyre'

LANGUAGE_CODE = 'eng-us'

LOCATION_CODE_CLASS = 'mwana.malawi.locations.LocationCode'
