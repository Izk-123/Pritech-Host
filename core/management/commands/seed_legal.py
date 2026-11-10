from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import LegalDocument


TERMS_HTML = """
<h2>1. Definitions</h2>
<p>"Pritech", "we", "us" refers to Pritech ICT Solutions. "Customer", "you" refers to the entity or individual using our services. "Services" means the web hosting, email, and related services provided by Pritech.</p>

<h2>2. Service Description</h2>
<p>Pritech provides shared and managed hosting services, domains, SSL certificates, and email accounts. The specific features and limits are governed by the plan you selected at the time of purchase.</p>

<h2>3. Account Registration</h2>
<p>You agree to provide accurate, complete information at checkout and to keep it up to date. You are responsible for safeguarding your login credentials. Notify us immediately at info@pritechmw.com of any unauthorized use.</p>

<h2>4. Payment Terms</h2>
<p>Fees are quoted in Malawi Kwacha (MWK) and are payable in advance. Managed packages require a 25% deposit before work begins. Renewal invoices are issued 14 days before your subscription expires. Overdue accounts are subject to the dunning schedule described in Section 6.</p>

<h2>5. Refunds</h2>
<p>See our <a href="/legal/refund/">Refund Policy</a>.</p>

<h2>6. Renewals and Dunning</h2>
<p>We send reminders at 30, 14, 7, 3, and 1 day before expiry. After expiry, accounts enter a 7-day grace period. On day 8 after expiry, accounts are suspended. On day 45 after expiry, account data is permanently deleted. You can restore a suspended account at any time by paying the outstanding renewal invoice.</p>

<h2>7. Acceptable Use</h2>
<p>Your use of the Services is governed by our <a href="/legal/aup/">Acceptable Use Policy</a>. Violation may result in immediate suspension or termination without refund.</p>

<h2>8. Data Protection</h2>
<p>Our handling of your personal data is governed by our <a href="/legal/privacy/">Privacy Policy</a>. You retain full ownership of the content you upload. You are responsible for ensuring you have the legal right to host any content you place on our servers.</p>

<h2>9. Backups</h2>
<p>Where your plan includes backups, they are provided as a convenience only. Pritech is not liable for any data loss. You are responsible for maintaining your own backups of critical content.</p>

<h2>10. Availability</h2>
<p>Our target availability is 99.5% per calendar month, excluding scheduled maintenance. Marketing materials must not claim a higher figure until redundancy and out-of-hours support are in place. We will notify you of planned maintenance with at least 24 hours' notice where possible.</p>

<h2>11. Limitation of Liability</h2>
<p>To the maximum extent permitted by Malawi law, Pritech's total liability under these Terms shall not exceed the fees you paid in the 12 months preceding the claim. We are not liable for indirect, incidental, or consequential damages, including lost profits or data.</p>

<h2>12. Termination</h2>
<p>You may cancel at any time from your dashboard. We may terminate your account for material breach (including non-payment or AUP violation) with the notice described in Section 6.</p>

<h2>13. Modifications</h2>
<p>We may update these Terms from time to time. Material changes will be communicated by email at least 14 days before taking effect. Continued use of the Services after the effective date constitutes acceptance.</p>

<h2>14. Governing Law</h2>
<p>These Terms are governed by the laws of the Republic of Malawi. Disputes will be resolved in the courts of Malawi, subject to any mandatory consumer protections.</p>

<h2>15. Contact</h2>
<p>Questions about these Terms: <a href="mailto:info@pritechmw.com">info@pritechmw.com</a> · +265 996 110 336 · Area 51/24 Kanengo, Lilongwe.</p>
"""


PRIVACY_HTML = """
<h2>1. Who We Are</h2>
<p>Pritech ICT Solutions ("Pritech") operates pritechhost.com. We are the data controller for the personal data described in this policy.</p>

<h2>2. Data We Collect</h2>
<ul>
    <li><strong>Account data:</strong> name, email, phone number, date of birth, gender, company name, TIN/VAT (if you provide them).</li>
    <li><strong>Billing data:</strong> invoice history, payment method type (mobile money / card / bank), last four digits of card (if applicable). We do not store full card numbers — these are handled by PayChangu.</li>
    <li><strong>Technical data:</strong> IP address, browser type, device type, access times, pages viewed.</li>
    <li><strong>Content data:</strong> files, databases, and emails you store on our servers.</li>
</ul>

<h2>3. How We Use Your Data</h2>
<ul>
    <li>To provide, operate, and maintain the Services.</li>
    <li>To process payments and issue invoices.</li>
    <li>To send service notifications, renewals, and support responses.</li>
    <li>To detect and prevent fraud, abuse, and security incidents.</li>
    <li>To comply with legal obligations under Malawi law.</li>
</ul>

<h2>4. Legal Basis</h2>
<p>We process personal data on the basis of (a) contract performance — you have engaged us to provide hosting; (b) legitimate interest — running and securing the service; and (c) consent — for optional analytics cookies and marketing communications.</p>

<h2>5. Sharing</h2>
<p>We share data only with:</p>
<ul>
    <li><strong>PayChangu</strong> — for payment processing.</li>
    <li><strong>HestiaCP</strong> — for server account provisioning (data is stored on servers controlled by us).</li>
    <li><strong>Email/SMS providers</strong> — for transactional notifications.</li>
    <li><strong>Registrars</strong> — when you register a domain through us (your registrant details are required by ICANN / .mw registry).</li>
    <li><strong>Law enforcement</strong> — only when required by valid legal process.</li>
</ul>
<p>We do not sell your data to third parties.</p>

<h2>6. Retention</h2>
<ul>
    <li>Account and billing records: retained for 7 years after your account closes, for tax and accounting reasons.</li>
    <li>Content data: deleted 45 days after subscription expiry (see Terms, Section 6).</li>
    <li>Support tickets: retained for 2 years after closure.</li>
    <li>Cookie consent records: retained for 2 years.</li>
</ul>

<h2>7. Your Rights</h2>
<p>Under Malawi data protection law you have the right to:</p>
<ul>
    <li>Access a copy of the personal data we hold about you.</li>
    <li>Correct inaccurate data.</li>
    <li>Request deletion (subject to our legal retention obligations).</li>
    <li>Object to processing, or restrict it.</li>
    <li>Data portability (export in a machine-readable format).</li>
</ul>
<p>To exercise any of these rights, email <a href="mailto:info@pritechmw.com">info@pritechmw.com</a>. We will respond within 30 days.</p>

<h2>8. Security</h2>
<p>We use industry-standard controls: encryption in transit (HTTPS), hashed passwords, access controls, and audit logs. No system is 100% secure, but we take reasonable steps to protect your data.</p>

<h2>9. Breach Notification</h2>
<p>If we become aware of a data breach that poses a risk to your rights, we will notify the relevant Malawi authority and affected users without undue delay.</p>

<h2>10. Cookies</h2>
<p>We use necessary cookies to run the site (login, payments, security) and optional cookies (analytics) only with your consent. You can change your consent at any time via the cookie banner.</p>

<h2>11. International Transfers</h2>
<p>Our servers are located in Malawi unless specified at signup. Where we transfer data outside Malawi (for example to an international email provider), we ensure appropriate safeguards are in place.</p>

<h2>12. Changes</h2>
<p>We will notify you of material changes to this policy by email at least 14 days before they take effect.</p>

<h2>13. Contact</h2>
<p>Data questions: <a href="mailto:info@pritechmw.com">info@pritechmw.com</a></p>
"""


AUP_HTML = """
<h2>1. Scope</h2>
<p>This Acceptable Use Policy (AUP) applies to all customers and users of Pritech Hosting services. Violation may result in immediate suspension, termination, or reporting to law enforcement, at our sole discretion.</p>

<h2>2. Prohibited Activities</h2>
<ul>
    <li><strong>Illegal content:</strong> hosting content that violates Malawi law, including child exploitation material, malware, phishing kits, and content that incites violence.</li>
    <li><strong>Network abuse:</strong> port scanning, vulnerability probing, DDoS traffic, hosting open proxies, Tor exit nodes, or VPN tunnels intended to hide illegal activity.</li>
    <li><strong>Cryptocurrency mining:</strong> including in browsers, on serverless functions, or via third-party scripts embedded in your website.</li>
    <li><strong>Spam and bulk email:</strong> unsolicited bulk email, unsolicited SMS, and affiliate spam. Email accounts are limited by your plan; exceeding them is grounds for suspension.</li>
    <li><strong>Resource abuse:</strong> sustained CPU or memory use above your plan limits, or patterns that degrade service for other customers.</li>
    <li><strong>Copyright infringement:</strong> hosting or distributing content you do not have the right to host.</li>
    <li><strong>Impersonation:</strong> impersonating another person or business, or registering domains in bad faith.</li>
</ul>

<h2>3. Server-Side Limits</h2>
<p>Each plan has a documented disk and bandwidth allowance. Applications that consume more than the plan allows may be throttled, suspended, or asked to upgrade. Resource-intensive tasks such as video transcoding, machine learning training, and long-running batch jobs must be approved in advance.</p>

<h2>4. Student Accounts</h2>
<p>Student hosting is offered at a subsidised rate for verified students only. Abuse of student pricing — for example, using it to host production websites for commercial entities — may result in the account being converted to a standard plan or terminated.</p>

<h2>5. Reporting Abuse</h2>
<p>To report abuse of Pritech services (spam originating from our network, phishing sites, copyright infringement), email <a href="mailto:abuse@pritechmw.com">abuse@pritechmw.com</a> with as much detail as possible. We will investigate and respond within 2 business days.</p>

<h2>6. Consequences</h2>
<p>Violations may result in:</p>
<ul>
    <li>Immediate suspension pending investigation.</li>
    <li>Permanent termination without refund.</li>
    <li>Notification to relevant authorities.</li>
    <li>Civil or criminal proceedings where warranted.</li>
</ul>

<h2>7. Appeals</h2>
<p>If you believe your account was suspended in error, email <a href="mailto:info@pritechmw.com">info@pritechmw.com</a> within 7 days with the details of your case.</p>

<h2>8. Contact</h2>
<p>Abuse reports: <a href="mailto:abuse@pritechmw.com">abuse@pritechmw.com</a> · General enquiries: <a href="mailto:info@pritechmw.com">info@pritechmw.com</a></p>
"""


REFUND_HTML = """
<h2>1. Deposit Refunds</h2>
<p>Managed website packages (Starter, Business, Enterprise) require a 25% deposit before work begins. The deposit is non-refundable once design work has started. If you cancel before work begins, we will refund the deposit less any payment processing fees.</p>

<h2>2. Subscription Refunds</h2>
<p>Hosting subscriptions (including self-service and student plans) may be cancelled at any time. Cancellation takes effect at the end of the current billing period. We do not provide pro-rata refunds for partial periods.</p>

<h2>3. Service Failure</h2>
<p>If we fail to meet our 99.5% uptime commitment in any given month, you may be eligible for a service credit equal to one month's fees for that month. Credits must be requested within 30 days of the incident.</p>

<h2>4. Domain Registrations</h2>
<p>Domain registration fees are non-refundable once the domain has been registered in your name. If we fail to register a domain you have paid for, we will refund the fee in full or offer an alternative domain of equal value.</p>

<h2>5. Exceptions</h2>
<p>Refunds will not be issued where:</p>
<ul>
    <li>The account was terminated for Acceptable Use Policy violation.</li>
    <li>The account was terminated for non-payment after the dunning period.</li>
    <li>The customer has already used a substantial portion of the service (e.g., >50% of the billing period has elapsed).</li>
</ul>

<h2>6. How to Request a Refund</h2>
<p>Email <a href="mailto:info@pritechmw.com">info@pritechmw.com</a> with your invoice number and reason for the request. We will respond within 5 business days.</p>

<h2>7. Payment Method</h2>
<p>Refunds are issued to the original payment method where possible. Airtel Money and TNM Mpamba refunds may take up to 7 days to appear.</p>
"""


DOCS = [
    ('terms',   'Terms of Service',        '1.0', TERMS_HTML),
    ('privacy', 'Privacy Policy',          '1.0', PRIVACY_HTML),
    ('aup',     'Acceptable Use Policy',   '1.0', AUP_HTML),
    ('refund',  'Refund Policy',           '1.0', REFUND_HTML),
]


class Command(BaseCommand):
    help = "Seed initial legal documents (Terms, Privacy, AUP, Refund)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help="Overwrite existing documents (creates new versions).",
        )

    def handle(self, *args, **opts):
        today = timezone.localdate()
        for slug, title, version, content in DOCS:
            existing = LegalDocument.current(slug)
            if existing and not opts['force']:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Skipping {slug} — {existing.title} v{existing.version} already active"
                    )
                )
                continue

            if existing:
                # Deactivate old version
                existing.is_active = False
                existing.save(update_fields=['is_active'])

            doc = LegalDocument.objects.create(
                slug=slug,
                title=title,
                version=version,
                content=content,
                effective_date=today,
                is_active=True,
            )
            self.stdout.write(self.style.SUCCESS(f"  ✔ Created {doc}"))

        self.stdout.write(self.style.SUCCESS("\nLegal documents seeded."))
        self.stdout.write(
            self.style.WARNING(
                "\n⚠  IMPORTANT: These are starting templates. "
                "Have a qualified lawyer review them before going fully live."
            )
        )