from app.utils.pragma_parser import parse_pragma_solidity


def test_parse_simple_pragma():
    source = 'pragma solidity ^0.8.24;\ncontract Foo {}'
    assert parse_pragma_solidity(source) == "0.8.24"


def test_parse_range_pragma():
    source = "pragma solidity >=0.8.0 <0.9.0;\ncontract Bar {}"
    assert parse_pragma_solidity(source) == "0.8.0"


def test_parse_multiple_pragmas():
    source = """
    pragma solidity ^0.8.0;
    // SPDX
    pragma solidity 0.8.24;
    contract Baz {}
    """
    assert parse_pragma_solidity(source) == "0.8.24"


def test_parse_equals_and_caret():
    source = "pragma solidity =0.8.19;\ncontract Foo {}"
    assert parse_pragma_solidity(source) == "0.8.19"
    assert parse_pragma_solidity("pragma solidity ^0.8.20;\ncontract Foo {}") == "0.8.20"


def test_parse_ignores_exclusive_upper_bound():
    source = "pragma solidity >=0.8.10 <0.8.24;\ncontract Foo {}"
    assert parse_pragma_solidity(source) == "0.8.10"


def test_parse_case_insensitive():
    source = "PRAGMA SOLIDITY ^0.8.12;\ncontract Foo {}"
    assert parse_pragma_solidity(source) == "0.8.12"


def test_parse_empty_source():
    assert parse_pragma_solidity("") is None
    assert parse_pragma_solidity("   ") is None
