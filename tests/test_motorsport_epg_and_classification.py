import pytest
from app.services.genre_classifier import genre_classifier
from app.services.italian_epg_service import italian_epg_service
from app.services.canonical_competition_service import detect_canonical_competition, is_tier_match


def test_italian_epg_motorsport_resolution():
    meta = {"name": "Sky Sport Uno", "default_silo": None, "default_cat": None}

    # Moto 3 and MotoGP
    cat1, silo1 = italian_epg_service._resolve_category_and_silo(meta, "GP Qatar: Moto 3")
    assert cat1 == "motori"
    assert silo1 == "motor-sports"

    cat2, silo2 = italian_epg_service._resolve_category_and_silo(meta, "GP Spagna: Moto 2")
    assert cat2 == "motori"
    assert silo2 == "motor-sports"

    cat3, silo3 = italian_epg_service._resolve_category_and_silo(meta, "Motociclismo: GP Italia - Gara")
    assert cat3 == "motori"
    assert silo3 == "motor-sports"

    # WEC and Endurance
    cat4, silo4 = italian_epg_service._resolve_category_and_silo(
        meta,
        "Automobilismo: World Championship Endurance 2026 6 ore di monza - Sessione mattutina"
    )
    assert cat4 == "motori"
    assert silo4 == "motor-sports"

    cat5, silo5 = italian_epg_service._resolve_category_and_silo(meta, "Automobilismo: 24 Ore di Le Mans")
    assert cat5 == "motori"
    assert silo5 == "motor-sports"

    cat6, silo6 = italian_epg_service._resolve_category_and_silo(meta, "WEC: 6 Ore di Spa")
    assert cat6 == "motori"
    assert silo6 == "motor-sports"

    # Formula 1
    cat7, silo7 = italian_epg_service._resolve_category_and_silo(meta, "GP Bahrain: Formula 1")
    assert cat7 == "motori"
    assert silo7 == "motor-sports"


def test_genre_classification_motorsport_events():
    cases = [
        ("GP Qatar: Moto 3", "motori", "MotoGP e Superbike"),
        ("GP Spagna: Moto 2", "motori", "MotoGP e Superbike"),
        ("Motociclismo: GP Italia - Gara", "motori", "MotoGP e Superbike"),
        ("Automobilismo: World Championship Endurance 2026 6 ore di monza - Sessione mattutina", "motori", "NASCAR e IndyCar"),
        ("Automobilismo: 24 Ore di Le Mans", "motori", "NASCAR e IndyCar"),
        ("WEC: 6 Ore di Spa", "motori", "NASCAR e IndyCar"),
        ("GP Bahrain: Formula 1", "motori", "Formula 1"),
        ("Rally di Montecarlo: WRC - Prova Speciale", "motori", "Rally e WRC"),
    ]

    for title, exp_cat, exp_genre in cases:
        # Test directly with motor-sports silo
        m = {"title": title, "category": exp_cat, "_silo": "motor-sports"}
        c_cat, c_genre = genre_classifier.classify(m)
        assert c_cat == exp_cat, f"Expected {exp_cat} for '{title}', got {c_cat}"
        assert c_genre == exp_genre, f"Expected {exp_genre} for '{title}', got {c_genre}"

        # Test recovery even if category was generic altri_sport
        m_generic = {"title": title, "category": "altri_sport", "_silo": "altri_sport"}
        c_cat2, c_genre2 = genre_classifier.classify(m_generic)
        assert c_cat2 == exp_cat, f"Generic fallback expected {exp_cat} for '{title}', got {c_cat2}"
        assert c_genre2 == exp_genre, f"Generic fallback expected {exp_genre} for '{title}', got {c_genre2}"


def test_canonical_competition_motorsport_matching():
    slug1 = detect_canonical_competition("GP Qatar: Moto 3", silo_hint="motor-sports")
    assert slug1 == "motorsport_motogp"

    slug2 = detect_canonical_competition("World Championship Endurance 6 Ore di Monza", silo_hint="motor-sports")
    assert slug2 == "motorsport_wec"

    slug3 = detect_canonical_competition("Formula 1 GP Bahrain", silo_hint="motor-sports")
    assert slug3 == "motorsport_f1"
