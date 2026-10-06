"""Fictional DEMO content for the sandbox: communities, threads around the
fixture mentions, filler comment pools, and the demo guide pages.

Everything here is invented for demos. Handles are fictional; community names
are generic (no real community names). Filler comments share experiences and
point people to their own prescriber; none recommends a dose.
"""

from urllib.parse import urlsplit

GUIDES_PREFIX = "/smart-patient-guides"
MENTION = "@mention"  # placeholder for the demo mention inside a thread spec

COMMUNITIES = {
    # name: (kind, title, description)
    "tirzepatide_talk": ("forum", "Tirzepatide talk", "Experiences, questions and provider stories. "
                         "No sourcing, no dosing advice."),
    "glp1_journey": ("forum", "GLP-1 journey", "Check-ins, plateaus and wins from people on GLP-1 programs."),
    "hairloss_help": ("forum", "Hair loss help", "Treatments, timelines and patience."),
    "telehealth_reviews": ("forum", "Telehealth reviews", "Honest reviews of online care programs."),
    "menshealth_q": ("forum", "Men's health Q&A", "Questions about men's health programs."),
    "glp1_support_group": ("forum", "GLP-1 support group", "A friendly support group for members of any "
                           "program."),
    "peptide_questions": ("forum", "Peptide questions", "Questions about peptide therapies. Talk to a "
                          "licensed prescriber."),
    "quick_takes": ("forum", "Quick takes", "Short posts, links and hot takes."),
    "wellpeps": ("review", "WellPeps (demo listing)", "Fictional reviews of WellPeps for demos."),
    "telehealth_providers": ("review", "Telehealth providers (demo listing)",
                             "Fictional reviews of online care providers."),
}

# Photo "communities" are accounts; the seeder adds the ones it uses.
PHOTO_ACCOUNTS = {
    "slimscript_rx": "Fall reset: start your weight program this month. Message us for details.",
    "glowpath_health": "Real members, real routines. Tell us about your week in the comments.",
    "wellpeps_demo": "Questions about your membership? Our support team answers in the member portal.",
}

# --- Threads around the fixture mentions (keyed by fixture external_id) -------------
#
# "comments": (author, body, points, parent index or None); MENTION marks where
# the fixture mention sits when it is a comment (role "comment").

FIXTURE_THREADS: dict[str, dict] = {
    "tp-wp-0001": {
        "kind": "review", "community": "wellpeps", "role": "op", "rating": 1,
        "comments": [
            ("verified_member_ty", "Same canned email here. It took a week before a real person answered.", 3, None),
            ("kel_in_ohio", "Did they ever give you a tracking number?", 1, None),
        ],
    },
    "ig-c-17900000000000001": {
        "kind": "photo", "role": "op",
        "comments": [
            ("sunny.side.steps", "Love this update! Fourteen pounds is huge.", 12, None),
            ("marcus_lifts_daily", "Which program is this?", 3, None),
            ("fixture_glp_journey", "@marcus_lifts_daily a telehealth program, happy to answer questions", 2, 1),
            ("plantbased_pam", "The check-ins make such a difference.", 5, None),
        ],
    },
    "t1_fxhims01": {
        "kind": "forum", "community": "glp1_journey", "role": "comment", "flair": "Shipping",
        "title": "Shipping delays lately?",
        "op": ("rainy_day_reader", "Is anyone else seeing refill shipments take way longer than usual this "
               "month? Trying to figure out if it's just me.", 57),
        "comments": [
            ("cozy_cardigan_kat", "Mine came on time but the tracking was useless until the day it arrived.", 14, None),
            (MENTION, None, 41, None),
            ("nightshift_nate", "Same company, same problem. I ended up calling and they reshipped.", 9, 1),
            ("rainy_day_reader", "Nine days with no movement is rough. Did support say anything?", 6, 1),
            ("budget_brenda", "Different company here and no issues, for what it's worth.", 3, None),
        ],
    },
    "t3_fxro0002": {
        "kind": "forum", "community": "telehealth_reviews", "role": "op", "flair": "Billing", "points": 88,
        "comments": [
            ("frugal_fern", "Intro pricing that jumps later is so common now. Always check the month-two price.", 31, None),
            ("ledger_lou", "Screenshot the checkout page next time. It helped me get a partial refund once.", 12, 0),
            ("west_end_walker", "Did you get any email before the renewal?", 4, None),
        ],
    },
    "fb-cmt-100000000000001": {
        "kind": "forum", "community": "glp1_support_group", "role": "comment", "flair": "Help",
        "title": "Anyone else stuck trying to cancel a subscription?",
        "op": ("garden_gloves_gail", "Posting for my sister. She has been trying to cancel her telehealth "
               "plan and keeps getting the runaround. Any tips that actually worked?", 23),
        "comments": [
            ("porchlight_pete", "Email AND call, then keep a log with dates. That finally worked for me.", 11, None),
            (MENTION, None, 17, None),
            ("garden_gloves_gail", "That is exactly what she is dealing with. Billed again after asking to cancel.", 5, 1),
            ("card_dispute_dana", "If they keep billing after you cancelled in writing, your bank can help.", 8, 1),
        ],
    },
    "tt-7400000000000000001": {
        "kind": "photo", "role": "op", "video": True,
        "comments": [
            ("tea_and_treadmills", "the surprise lab fee got me too", 220, None),
            ("kayla_cooks_light", "how long did shipping take in the end?", 41, None),
            ("fixture_mochi_diary", "@kayla_cooks_light about two weeks total", 18, 1),
            ("quietmornings_88", "part 2 when?", 9, None),
        ],
    },
    # The scenario thread for the demo script.
    "t3_fxpi0003": {
        "kind": "forum", "community": "tirzepatide_talk", "role": "op", "flair": "Question", "points": 23,
        "comments": [
            ("maple_and_miles", "I went through one of the bigger telehealth programs last year. Intake was quick, "
             "but follow-ups after the first month were billed separately. Read the fine print on what "
             "'ongoing care' means.", 18, None),
            ("blue_kettle_jo", "Same experience. The 'all-inclusive' price didn't include labs either.", 9, 0),
            ("trailmix_theo", "Mine included a check-in every four weeks, but it was a form, not a conversation "
             "with a person. Worked fine for me honestly.", 7, None),
            ("night_owl_nina", "Ask them directly whether messaging the provider costs extra. That was the "
             "surprise for me.", 12, None),
            ("cautious_carl", "Also ask which pharmacy fills it. Mine switched pharmacies halfway through and "
             "nobody told me.", 6, 3),
            ("sunny_sloane", "I'd loop in your regular doctor too. Mine had questions about compounded versions "
             "that were worth hearing.", 5, None),
        ],
    },
    "t3_fxpi0004": {
        "kind": "forum", "community": "glp1_journey", "role": "op", "flair": "Question", "points": 15,
        "comments": [
            ("reformed_skeptic", "Haven't used them, but ask any program how often you actually talk to a provider.", 8, None),
            ("lunchbox_lena", "Following. Also curious about real member experiences.", 3, None),
            ("data_dan_42", "Check the reviews, but take the five-star ones with a grain of salt.", 4, 0),
        ],
    },
    "t3_fxae0005": {
        "kind": "forum", "community": "glp1_journey", "role": "op", "flair": "Side effects", "points": 64,
        "comments": [
            ("steady_steph", "Please follow up with your prescriber today and tell them about the ER visit. "
             "That's not something to wait on.", 44, None),
            ("night_nurse_noor", "Glad you went in. Make sure whoever prescribes it knows what happened.", 29, None),
            ("hopeful_hal", "Hope you're feeling better. Update us when you can.", 7, None),
        ],
    },
    "fb-post-100000000000002": {
        "kind": "photo", "role": "comment", "account": "wellpeps_demo",
        "comments": [
            ("autumn_audrey", "Thanks for the reminder, support answered me within a day.", 4, None),
            (MENTION, None, 17, None),
            ("pineapple_pat", "Same issue as above, still waiting on a refund.", 6, 1),
        ],
    },
    "t3_fxpv0006": {
        "kind": "forum", "community": "telehealth_reviews", "role": "op", "flair": "Privacy", "points": 46,
        "comments": [
            ("privacy_first_paz", "Ads right after a health intake form are creepy. Check the privacy policy for "
             "'sharing with partners'.", 22, None),
            ("cookie_crumb_cal", "Could also be the browser. Did you search GLP-1 stuff in the same browser?", 9, None),
            ("privacy_first_paz", "Good point, worth checking both.", 3, 1),
        ],
    },
    "tp-wp-0002": {
        "kind": "review", "community": "wellpeps", "role": "op", "rating": 1,
        "comments": [
            ("careful_carmen", "Did the bank refund both?", 2, None),
            ("ledger_lou", "Billing after a pause seems to be an industry-wide problem.", 1, None),
        ],
    },
    "t1_fxmi0007": {
        "kind": "forum", "community": "peptide_questions", "role": "comment", "flair": "Discussion",
        "title": "Shoulder injury, what are my options?",
        "op": ("weekend_warrior_wes", "Tore something in my shoulder lifting. Physio is slow. Anyone have "
               "experience with peptide clinics, or is that hype?", 19),
        "comments": [
            ("pt_paula", "A good physio plan is slow but it works. I'd be wary of anything promising shortcuts.", 19, None),
            (MENTION, None, 2, None),
            ("skeptical_sid", "Please don't inject research chemicals bought online. Talk to a doctor.", 25, 1),
            ("weekend_warrior_wes", "Yeah, not doing anything without a prescriber involved.", 8, 1),
        ],
    },
    "ig-c-17900000000000002": {
        "kind": "photo", "role": "op",
        "comments": [
            ("soccer_mom_sam", "Go team!", 14, None),
            ("coach_carlos", "Great effort from everyone this weekend.", 9, None),
        ],
    },
    "1830000000000000001": {
        "kind": "forum", "community": "quick_takes", "role": "op", "points": 2,
        "comments": [
            ("scam_spotter", "Reported. Never connect a wallet to a link in a bio.", 30, None),
            ("lurker_lin", "This has nothing to do with the clinic, it's a bot.", 12, None),
        ],
    },
    "tt-7400000000000000002": {
        "kind": "photo", "role": "op", "video": True,
        "comments": [
            ("weigh_in_wendy", "the flat price thing is a big deal for budgeting", 88, None),
            ("cost_curious_cam", "does flat pricing include the follow-ups though?", 31, None),
            ("fixture_price_check", "@cost_curious_cam good question, checking", 12, 1),
        ],
    },
    "t3_fxro0008": {
        "kind": "forum", "community": "hairloss_help", "role": "op", "flair": "Question", "points": 12,
        "comments": [
            ("crown_cowlick", "Ask whether they do bloodwork first. Mine didn't and I wish they had.", 15, None),
            ("hairline_hank", "I went with my dermatologist instead, slower but I trust them.", 9, None),
            ("crown_cowlick", "Fair, that's probably the safest route.", 2, 1),
        ],
    },
    "fb-cmt-100000000000003": {
        "kind": "forum", "community": "glp1_support_group", "role": "comment", "flair": "Help",
        "title": "Refund experiences?",
        "op": ("linen_and_lattes", "Has anyone actually gotten a refund for a damaged shipment? What did you "
               "have to do?", 14),
        "comments": [
            (MENTION, None, 9, None),
            ("porchlight_pete", "Photos of the package and the temperature strip helped my case.", 6, 0),
            ("frugal_fern", "Six weeks is ridiculous. Ask for a supervisor.", 4, 0),
        ],
    },
    "tp-wp-0003": {
        "kind": "review", "community": "wellpeps", "role": "op", "rating": 5,
        "comments": [
            ("new_member_mo", "Good to hear. Starting next week.", 2, None),
            ("kel_in_ohio", "Mine was similar, quick intake.", 1, None),
        ],
    },
    "t3_fxsw0009": {
        "kind": "forum", "community": "tirzepatide_talk", "role": "op", "flair": "Discussion", "points": 19,
        "comments": [
            ("maple_and_miles", "Ask any new provider how follow-ups work before you sign up.", 10, None),
            ("loyal_lucy", "I switched last spring. The transfer was smoother than expected.", 6, None),
            ("budget_brenda", "Plan the switch so you're not left waiting between providers.", 4, 1),
        ],
    },
    "ig-c-17900000000000003": {
        "kind": "photo", "role": "op",
        "comments": [
            ("sorry_to_hear_sue", "A month with no reply is awful.", 60, None),
            ("wait_and_see_will", "Was about to sign up, thanks for sharing.", 33, None),
            ("balanced_bea", "My experience was fine, but sorry this happened to you.", 12, None),
        ],
    },
    "tt-7400000000000000003": {
        "kind": "photo", "role": "op", "video": True,
        "comments": [
            ("big_sis_bri", "please don't, talk to your parents and a real doctor", 140, None),
            ("community_mod_demo", "Reminder: prescription medicines need a prescriber. Please talk to a "
             "parent and a doctor.", 5, None),
        ],
    },
    "blog-fx-0010": {
        "kind": "forum", "community": "telehealth_reviews", "role": "op", "flair": "Link", "points": 9,
        "comments": [
            ("spreadsheet_sara", "Nice roundup. Would love follow-up care costs in a column.", 7, None),
            ("frugal_fern", "Prices change monthly, so check the date on these.", 3, None),
        ],
    },
    "t3_fxpi0012": {
        "kind": "forum", "community": "menshealth_q", "role": "op", "flair": "Question", "points": 8,
        "comments": [
            ("quiet_quinn", "Smooth for me, the consult was a short questionnaire.", 6, None),
            ("direct_dave", "Ask your own doctor too, sometimes insurance covers it.", 4, None),
            ("quiet_quinn", "Good call, worth checking insurance first.", 1, 1),
        ],
    },
}

# --- Filler pools for the synthetic weekly threads ----------------------------------

FILLER_HANDLES = [
    "quietmornings_88", "lena_walks_far", "teapot_tom", "ridgeline_rae", "patient_pia", "kale_and_kettlebells",
    "midweek_marla", "slowcooker_sam", "porchswing_pat", "oatmeal_otto", "trail_tess", "cardigan_carl",
    "steady_stella", "bookclub_bea", "nightowl_ned", "garden_gus", "harbor_hana", "lunchbreak_lou",
    "pebble_path_pru", "sunday_reset_sy",
]

FILLERS = {
    "forum": [
        "Same here, the first few weeks were the hardest.",
        "Hang in there. Plateaus are so frustrating.",
        "Did support ever get back to you?",
        "Following, curious what others say.",
        "Mine took about ten days last time too.",
        "Worth asking your provider before changing anything.",
        "Logging meals helped me more than I expected.",
        "Thanks for posting, I thought it was just me.",
        "Calling instead of using the chat got me an answer faster.",
        "Had the same billing surprise. Check the renewal date.",
        "Glad it's going well for you!",
        "Keep a note of every support ticket number, it helps later.",
    ],
    "reply": [
        "Exactly my experience.",
        "Good tip, thank you.",
        "Did that end up working?",
        "Same company?",
        "This is helpful, saving it.",
        "Hope it gets sorted soon.",
    ],
    "photo": [
        "How long did shipping take for you?",
        "Is this available in every state?",
        "Love this!",
        "My order is still pending...",
        "What does it cost after the first month?",
        "Following for updates",
        "Do you talk to an actual provider?",
    ],
    "review": [
        "Same experience here.",
        "Thanks, this helped me decide.",
        "Did they ever respond?",
        "Mine was the opposite, but good to know.",
    ],
    "urgent_health": [
        "Please call your prescriber or get seen today. Don't wait this out.",
        "Glad you posted. Make sure the provider knows what happened.",
        "Hope you're feeling better soon. Update us if you can.",
        "If it gets worse, urgent care or the ER. Seriously.",
    ],
    "urgent_billing": [
        "Document everything and call your bank.",
        "Screenshots of every charge will help with the dispute.",
        "That's awful. Did they give any explanation?",
        "Put the cancellation request in writing if you haven't.",
    ],
}

WEEKLY_OP = ("community_bot", "Share wins, questions and frustrations from this week. Be kind: no sourcing, "
             "no dosing advice, talk to your own prescriber about your plan.")

# --- Demo guide pages --------------------------------------------------------------


def _guide_slug(url: str) -> str | None:
    path = urlsplit(url).path.rstrip("/")
    if path == GUIDES_PREFIX:
        return "index"
    if path.startswith(GUIDES_PREFIX + "/"):
        slug = path[len(GUIDES_PREFIX) + 1:]
        return slug if slug and "/" not in slug else None
    return None


def guides() -> list[dict]:
    """One DEMO guide page per guide link in the links registry."""
    from harvey import knowledge

    try:
        entries = knowledge.links()
    except knowledge.KnowledgeError:
        entries = ()
    out = []
    for link in entries:
        slug = _guide_slug(link.url)
        if slug is None:
            continue
        out.append({
            "slug": slug, "title": link.label, "link_id": link.id, "real_url": link.url,
            "paragraphs": [
                "DEMO GUIDE PAGE. This placeholder stands in for the published page so the demo never "
                "leaves this machine.",
                f"In production, a reply that cites this guide links to {link.url} with Pulse's UTM "
                "tracking (utm_source, utm_medium=social_reply, utm_campaign=pulse, utm_content=m<mention id>).",
                "The real guides are free and ask for an e-mail address before download. They cover "
                "questions to ask before choosing a provider.",
            ],
        })
    return out


def guide(slug: str) -> dict | None:
    return next((g for g in guides() if g["slug"] == slug), None)
