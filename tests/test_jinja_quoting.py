"""
Unit tests for `quote_bare_jinja`.

A YAML scalar that opens with `{` starts a flow mapping, so an unquoted
`{{ ... }}` fails to parse instead of reaching Ansible as a Jinja
expression. The repair runs only on documents that already fail to parse.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from rag.generator import quote_bare_jinja


def _parses(text: str) -> bool:
    try:
        yaml.safe_load(text)
        return True
    except yaml.YAMLError:
        return False


def test_bare_jinja_on_own_line_is_quoted_and_parses():
    broken = textwrap.dedent("""\
        ---
        - name: Create a namespace
          hosts: localhost
          tasks:
            - name: Apply
              kubernetes.core.k8s:
                definition:
                  metadata:
                    labels:
                      {{ namespace_label }}
        """)
    assert not _parses(broken)

    fixed, changes = quote_bare_jinja(broken)

    assert changes, "expected the repair to report a change"
    assert _parses(fixed)
    assert '"{{ namespace_label }}"' in fixed


def test_mapping_value_starting_with_jinja_is_quoted():
    broken = "---\n- name: P\n  hosts: localhost\n  vars:\n    region: {{ var_region }}\n"
    assert not _parses(broken)

    fixed, changes = quote_bare_jinja(broken)

    assert _parses(fixed)
    assert 'region: "{{ var_region }}"' in fixed
    assert len(changes) == 1


def test_sequence_item_starting_with_jinja_is_quoted():
    broken = "---\n- name: P\n  hosts: localhost\n  vars:\n    items:\n      - {{ var_item }}\n"
    assert not _parses(broken)

    fixed, _ = quote_bare_jinja(broken)

    assert _parses(fixed)
    assert '- "{{ var_item }}"' in fixed


def test_valid_document_is_returned_untouched():
    good = textwrap.dedent("""\
        ---
        - name: Play
          hosts: localhost
          tasks:
            - name: Say hi
              ansible.builtin.debug:
                msg: "{{ var_message }}"
        """)

    result, changes = quote_bare_jinja(good)

    assert result == good
    assert changes == []


def test_interior_jinja_is_left_alone():
    """`web-{{ env }}` is already a valid plain scalar."""
    good = "---\n- name: P\n  hosts: localhost\n  vars:\n    n: web-{{ env }}\n"
    assert _parses(good)

    result, changes = quote_bare_jinja(good)

    assert result == good
    assert changes == []


def test_block_scalar_contents_are_not_touched():
    broken = textwrap.dedent("""\
        ---
        - name: P
          hosts: localhost
          tasks:
            - name: Write template
              ansible.builtin.copy:
                content: |
                  {{ not_yaml_in_here }}
                dest: {{ var_dest }}
        """)
    fixed, changes = quote_bare_jinja(broken)

    assert _parses(fixed)
    # The literal block keeps its raw Jinja...
    assert "      {{ not_yaml_in_here }}" in fixed
    # ...while the real mapping value gets quoted.
    assert 'dest: "{{ var_dest }}"' in fixed
    assert len(changes) == 1


def test_trailing_comment_stays_outside_the_quotes():
    broken = "---\n- name: P\n  hosts: localhost\n  vars:\n    r: {{ var_r }}  # region\n"

    fixed, _ = quote_bare_jinja(broken)

    assert _parses(fixed)
    assert 'r: "{{ var_r }}"  # region' in fixed


def test_expression_with_both_quote_styles_is_left_alone():
    """Nothing safe to wrap it in, so the draft is returned unchanged."""
    broken = """---\n- name: P\n  hosts: localhost\n  vars:\n    v: {{ lookup('env', "A") }}\n"""

    result, changes = quote_bare_jinja(broken)

    assert result == broken
    assert changes == []


def test_document_broken_for_other_reasons_is_returned_unchanged():
    """Quoting is not allowed to mask an unrelated syntax error."""
    broken = "---\n- name: P\n   hosts: localhost\n  bad_indent: [1, 2\n"

    result, changes = quote_bare_jinja(broken)

    assert result == broken
    assert changes == []


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_empty_input_is_safe(text):
    assert quote_bare_jinja(text) == (text, [])
