"""The defaults of the rules that name categories, canonical items and budget groups (issue #162), in one place for the app.
Equal to build_ledger.RULES (tests/test_role_literals.py checks it); the pipeline scripts keep their own copies, being standalone."""
ROLE_DEFAULTS = {
    "paycheck_items": ["Paycheck"], "recurring_categories": ["Subscriptions", "Utilities", "Insurance"],   # role default
    "plan_keep_categories": ["Housing", "Debt", "Insurance"], "income_groups": ["Income"], "debt_groups": ["Debt"],   # role default
    "loan_categories": ["Family Loan"],   # role default
    "goal_giving_categories": ["Giving"], "goal_housing_categories": ["Housing", "Utilities"],   # role default
}
