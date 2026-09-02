# NYS Fire Training Scheduler

A standalone, statewide-ready course and instructor coordination system for participating New York State counties and instructors assigned to the New York State Academy of Fire Science.

## Current product slice

- Statewide dashboard and schedule awareness
- Course library with a persistent Course Record Number and instructor staffing requirements
- County and Academy instructor directory
- Course-specific instructor authorizations
- Instructor self-registration with Site Administrator account approval
- Required SFI number during self-registration, optional for administrator-created records
- Possible-profile matching and explicit merge during application approval
- Login-only Site Administrator accounts and scheduling-only instructor records
- Instructor-submitted course authorization claims with State verification
- Statewide user management and county/organization administrator assignments
- Site Administrator management of participating counties and organizations
- Case-insensitive unique account emails and duplicate-account consolidation
- Self-service password recovery and Site Administrator password resets
- Email notifications for instructor assignments, removals, schedule changes, cancellations, account approval, and authorization approval
- Instructor-controlled notification preferences with explicit text-message consent
- Twilio SMS delivery with auditable delivery history
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
- `EMAIL_BACKEND=config.email_backend.CloudflareEmailBackend`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_EMAIL_API_TOKEN`, and `DEFAULT_FROM_EMAIL` for Cloudflare password-reset email delivery over HTTPS
- `NOTIFICATION_EMAIL_ENABLED=true` to enable operational email notices
- `SITE_BASE_URL=https://scheduling.nysfiretools.com` for links in notifications
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and either `TWILIO_MESSAGING_SERVICE_SID` or `TWILIO_FROM_NUMBER` for opt-in texts

The included Dockerfile runs migrations and starts Gunicorn. The Railway health check uses `/health/`.

After deployment succeeds, add `scheduling.nysfiretools.com` as a Railway custom domain and copy Railway's CNAME and TXT verification records to the existing DNS provider.

## Administration

Django superusers provide statewide/system administration. Organization administrators receive an `Organization administrator` role for the county or Academy they manage. County-scoped editing is enforced in application views; schedule visibility remains shared.
