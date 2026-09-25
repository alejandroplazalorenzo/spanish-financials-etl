import pytest

from sfetl.ownership import (
    CompanyIndex,
    CompanyRef,
    GleifParent,
    clean_parent_name,
    name_key,
    parse_gleif_raw,
    resolve_esef_statement,
    resolve_gleif_parent,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alfa Holding, S.L.", "Alfa Holding, S.L."),
        ("  Alfa  Holding,\xa0S.L.  ", "Alfa Holding, S.L."),
        # HTML spans inside words (converter output)
        (
            'A<span class="_ _3"></span>lfa <span class="x"> </span>Holding, S.L.',
            "Alfa Holding, S.L.",
        ),
        ("Alfa Holding, S.A. (en adelante, la Sociedad Dominante)", "Alfa Holding, S.A."),
        ("Alfa Holding, S.A. es la Sociedad dominante", "Alfa Holding, S.A."),
        (
            "Alfa Holding, S.A., Sociedad Dominante del Grupo Alfa, (en adelante Alfa)",
            "Alfa Holding, S.A.",
        ),
        (
            "La Sociedad dominante está controlada por Beta Inversiones, S.L., domiciliada en "
            "Madrid, siendo ésta la dominante última del Grupo.",
            "Beta Inversiones, S.L.",
        ),
        (
            "La Sociedad Dominante forma parte, a su vez, de un grupo encabezado por su socio "
            "mayoritario Beta Inversiones, S.L.,",
            "Beta Inversiones, S.L.",
        ),
        (
            "La Sociedad dominante, Alfa Real Estate, S.A., es matriz de un grupo",
            "Alfa Real Estate, S.A.",
        ),
        (
            "Alfa Corporación, S.A. (antes Gamma, S.A.) cambió su denominación en 2023.",
            "Alfa Corporación, S.A.",
        ),
        ("Industria Alfa, S.A. (Alfa)", "Industria Alfa, S.A."),
    ],
)
def test_noisy_parent_names_are_cleaned(raw: str, expected: str) -> None:
    assert clean_parent_name(raw) == (expected, "named")


@pytest.mark.parametrize("raw", ["No hay", "Nombre de la dominante última del grupo: No hay", "-"])
def test_no_parent_is_declared(raw: str) -> None:
    assert clean_parent_name(raw) == (None, "none_declared")


def test_a_description_is_not_taken_for_a_name() -> None:
    raw = (
        "Alfa es una empresa de agua y energía integrada vertical y horizontalmente, líder en "
        "infraestructuras y gestión de recursos, especializada en tecnologías de desalación"
    )
    assert clean_parent_name(raw) == (None, "unparsed")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Enel Iberia, S.L.U.", "ENEL IBERIA SL"),
        ("Alfa, S.p.A.", "ALFA - SPA"),
        ("Compañía Alfa de Seguridad, S.A.", "COMPAÑIA ALFA DE SEGURIDAD SA"),
        ("Alfa Holding, Sociedad Anónima", "ALFA HOLDING S.A."),
        ("Grupo Beta S.A.P.I. de C.V.", "GRUPO BETA"),
    ],
)
def test_name_key_ignores_case_accents_punctuation_and_legal_form(a: str, b: str) -> None:
    assert name_key(a) == name_key(b)


def test_name_key_keeps_different_companies_apart() -> None:
    assert name_key("Alfa Cash, S.A.") != name_key("Alfa, S.A.")


FILER = CompanyRef(1, "LEIFILER000000000001", "ALFA CASH, S.A.")
PARENT = CompanyRef(2, "LEIPARENT00000000002", "COMPAÑÍA ALFA, S.A.")
INDEX = CompanyIndex([FILER, PARENT])


def test_self_reference_is_resolved_to_the_filer() -> None:
    resolved = resolve_esef_statement(FILER, "Alfa Cash, S.A.", "named", None, INDEX)
    assert resolved.resolution == "self" and resolved.parent_company_id == FILER.company_id


def test_parent_by_normalised_name() -> None:
    resolved = resolve_esef_statement(FILER, "Compañía Alfa, S.A.", "named", None, INDEX)
    assert resolved.resolution == "name" and resolved.parent_company_id == 2


def test_lei_from_gleif_wins_when_its_name_matches() -> None:
    gleif = GleifParent("direct", PARENT.lei, "COMPANIA ALFA SA", None)
    resolved = resolve_esef_statement(FILER, "Compañía Alfa, S.A.", "named", gleif, INDEX)
    assert resolved.resolution == "lei" and resolved.parent_lei == PARENT.lei


def test_unknown_parent_is_kept_unresolved_with_the_lei_when_known() -> None:
    gleif = GleifParent("ultimate", "LEIOUTSIDE0000000003", "DELTA SE", None)
    resolved = resolve_esef_statement(FILER, "Delta SE", "named", gleif, INDEX)
    assert resolved.resolution == "unresolved"
    assert resolved.parent_company_id is None and resolved.parent_lei == "LEIOUTSIDE0000000003"
    assert resolve_esef_statement(FILER, "Omega, S.L.", "named", None, INDEX).resolution == (
        "unresolved"
    )


def test_ambiguous_names_never_match() -> None:
    twins = CompanyIndex([FILER, CompanyRef(3, "L3", "Beta, S.A."), CompanyRef(4, "L4", "BETA SA")])
    assert twins.by_name("Beta S.A.") is None


def test_gleif_relationships_and_exceptions() -> None:
    raw = {
        "lei": FILER.lei,
        "direct": {
            "data": {
                "attributes": {
                    "lei": PARENT.lei,
                    "entity": {"legalName": {"name": "COMPAÑIA ALFA SA"}},
                }
            }
        },
        "direct_exception": None,
        "ultimate": None,
        "ultimate_exception": {"data": {"attributes": {"reason": "NATURAL_PERSONS"}}},
    }
    record = parse_gleif_raw(raw)
    direct, ultimate = record.for_relation("direct"), record.for_relation("ultimate")
    assert direct is not None and direct.parent_lei == PARENT.lei
    assert ultimate is not None and ultimate.exception == "NATURAL_PERSONS"
    assert resolve_gleif_parent(FILER, direct, INDEX).resolution == "lei"
    assert resolve_gleif_parent(FILER, ultimate, INDEX).resolution == "none_declared"


def test_initials_spelled_apart_still_match_the_register() -> None:
    gleif = GleifParent("direct", "LEIOUTSIDE0000000009", "F C Y C SOCIEDAD ANONIMA", None)
    resolved = resolve_esef_statement(FILER, "FCyC, S.A.", "named", gleif, INDEX)
    assert resolved.parent_lei == "LEIOUTSIDE0000000009"
