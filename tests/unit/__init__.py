"""vNext unit and property contract tests.

These tests intentionally never import the legacy runtime.  Until the vNext
package exists, the contract tests report explicit skips; once a vNext module
is present, missing protocol symbols are failures rather than silent passes.
"""
