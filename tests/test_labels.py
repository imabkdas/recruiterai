"""Display labels keep location out of the job title."""

from jobpilot.pipeline.labels import listing_labels


def test_linkedin_alert_location_leaves_the_title() -> None:
    company, title, location = listing_labels(
        "Unknown",
        "Full Stack EngineerAccenture services Pvt Ltd · Gurugram43 company alumni",
        None,
    )
    assert title == "Full Stack Engineer"
    assert company == "Accenture services Pvt Ltd"
    assert location == "Gurugram"


def test_linkedin_noise_and_onsite_suffix_are_not_the_location() -> None:
    company, title, location = listing_labels(
        "Unknown",
        "Java DeveloperTata Consultancy Services · Bengaluru (On-site)Easy Apply",
        None,
    )
    assert title == "Java Developer"
    assert company == "Tata Consultancy Services"
    assert location == "Bengaluru (On-site)"


def test_all_caps_title_still_splits_the_glued_company() -> None:
    company, title, location = listing_labels(
        "Unknown",
        "Fullstack Java With React-Software Engineer IIIDeloitte · Bengaluru7 connections",
        None,
    )
    assert title == "Fullstack Java With React-Software Engineer III"
    assert company == "Deloitte"
    assert location == "Bengaluru"


def test_acronym_company_is_not_split_at_an_inner_capital() -> None:
    company, title, location = listing_labels(
        "Unknown",
        "Software Engineer II - Java, Springboot, ReactJPMorganChase · Bengaluru (On-site)4,489 company alumni",
        None,
    )
    assert title == "Software Engineer II - Java, Springboot, React"
    assert company == "JPMorganChase"
    assert location == "Bengaluru (On-site)"


def test_pwc_stays_one_company_name() -> None:
    company, title, location = listing_labels(
        "Unknown",
        "JAVA FULL STACK DEVELOPER -Consultant -GurugramPwC India · GurugramActively recruiting",
        None,
    )
    assert company == "PwC India"
    assert title == "JAVA FULL STACK DEVELOPER -Consultant"
    assert location == "Gurugram"


def test_job_code_is_not_treated_as_a_company() -> None:
    company, title, location = listing_labels(
        "Unknown",
        "BCM-FINACLE-Java-Spingboot-Banking Domain-Staff-GDSF02EY · Gurugram (On-site)Actively recruiting",
        None,
    )
    assert title == "BCM-FINACLE-Java-Spingboot-Banking Domain-Staff-GDSF02EY"
    assert company == "Unknown"
    assert location == "Gurugram (On-site)"


def test_stored_location_stays_when_the_title_is_already_clean() -> None:
    company, title, location = listing_labels(
        "Northwind",
        "Java Engineer",
        "Bangalore",
    )
    assert company == "Northwind"
    assert title == "Java Engineer"
    assert location == "Bangalore"


def test_stored_location_wins_over_text_after_the_middot() -> None:
    _company, title, location = listing_labels(
        "Oracle",
        "Senior Java Backend Engineer · HyderabadActively recruiting",
        "Bengaluru, Karnataka, India",
    )
    assert title == "Senior Java Backend Engineer"
    assert location == "Bengaluru, Karnataka, India"
