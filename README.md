# NYS Fire Training Scheduler

A standalone, statewide-ready course and instructor coordination system for participating New York State counties and instructors assigned to the New York State Academy of Fire Science.

## Current product slice

- Statewide dashboard and schedule awareness
- Course library with a persistent Course Record Number and instructor staffing requirements
- County and Academy instructor directory
- Course-specific instructor authorizations
- Instructor self-registration with State Admin account approval
- Instructor-submitted course authorization claims with State verification
- Statewide user management and county/organization administrator assignments
- State administrator management of participating counties and organizations
- Case-insensitive unique account emails and duplicate-account consolidation
- Self-service password recovery and State administrator password resets
- Instructor travel preferences
- Instructor-entered preferred, tentative, and unavailable time windows
- Monday–Sunday availability view with visible daily time windows
- Repeating weekly availability rules with optional end dates
- Purposed, confirmed, completed, and cancelled training
- Unique Course Offering Numbers required before confirmation
- Multi-session training dates
- Hard instructor double-booking validation
- Qualified-instructor matching
- County-scoped administrator permissions
- Optional external Acadis registration link

Student registration, rosters, LMS features, and Acadis data synchronization are intentionally out of scope.

## Local setup

1. Create and activate a Python 3.14 virtual environment.
2. Install `requirements.txt`.
3. Run `python manage.py migrate`.
4. Optionally run `python manage.py seed_demo` for demonstration data.
5. Run `python manage.py runserver`.

SQLite is used locally when `DATABASE_URL` is absent. Railway supplies PostgreSQL through `DATABASE_URL` in production.

## Railway

Create a Railway project with an application service and PostgreSQL service. Configure:

- `DEBUG=false`
- `SECRET_KEY` as a long random value
- `ALLOWED_HOSTS=scheduling.nysfiretools.com,<railway-generated-host>`
- `CSRF_TRUSTED_ORIGINS=https://scheduling.nysfiretools.com,https://<railway-generated-host>`
- `DATABASE_URL` as a reference to the Railway PostgreSQL service
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `DEFAULT_FROM_EMAIL` for password-reset email delivery

The included Dockerfile runs migrations and starts Gunicorn. The Railway health check uses `/health/`.

After deployment succeeds, add `scheduling.nysfiretools.com` as a Railway custom domain and copy Railway's CNAME and TXT verification records to the existing DNS provider.

## Administration

Django superusers provide statewide/system administration. Organization administrators receive an `Organization administrator` role for the county or Academy they manage. County-scoped editing is enforced in application views; schedule visibility remains shared.
