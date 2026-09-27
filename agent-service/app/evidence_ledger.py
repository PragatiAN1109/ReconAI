"""A record of what the tools actually returned during one investigation.

This exists to answer one question: *did the model actually see the thing it
cited?*

A model asked for its sources will produce plausible ones whether or not it saw
them. ``FR-999`` looks exactly like ``FR-14``. The only defence is to remember
what was genuinely retrieved and check citations against it, so grounding is
decided by the application rather than asserted by the model.

Scoped to a single investigation run and held in memory. Nothing here is
persisted in this phase.
"""

from dataclasses import dataclass, field

from app.evidence_models import FeeRuleEvidence, SettlementEvidence, TransactionEvidence
from app.investigation_models import EvidenceReference, EvidenceSource
from app.policy_search import PolicyEvidence


@dataclass
class EvidenceLedger:
    """Identifiers of evidence retrieved during one investigation."""

    transactions: set[str] = field(default_factory=set)
    settlements: set[str] = field(default_factory=set)
    fee_rules: set[str] = field(default_factory=set)
    #: Policy documents, and the specific sections retrieved from each.
    policy_documents: set[str] = field(default_factory=set)
    policy_sections: set[tuple[str, str]] = field(default_factory=set)
    #: The text of each retrieved section, keyed by (document, section).
    #: Kept so a cited excerpt can be stored with the recommendation: a reviewer
    #: reading it months later should see the words the investigation actually
    #: saw, not whatever the corpus says by then.
    policy_excerpts: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (
            self.transactions or self.settlements or self.fee_rules or self.policy_documents
        )

    def record_transaction(self, transaction: TransactionEvidence) -> None:
        self.transactions.add(transaction.transaction_id)

    def record_settlements(self, settlements: list[SettlementEvidence]) -> None:
        for settlement in settlements:
            self.settlements.add(settlement.settlement_id)
            # The settlement names its transaction, and seeing it here is as
            # good as having fetched it.
            self.transactions.add(settlement.transaction_id)

    def record_fee_rules(self, fee_rules: list[FeeRuleEvidence]) -> None:
        for rule in fee_rules:
            self.fee_rules.add(rule.rule_id)

    def record_policies(self, policies: list[PolicyEvidence]) -> None:
        for policy in policies:
            self.policy_documents.add(policy.document_id)
            self.policy_sections.add((policy.document_id, policy.section))
            self.policy_excerpts[(policy.document_id, policy.section)] = policy.excerpt

    def supports(self, reference: EvidenceReference) -> bool:
        """True when this reference points at evidence actually retrieved.

        A policy citation naming a section must match a section that was really
        returned. Citing the right document and the wrong section is still a
        claim about text nobody read.
        """
        match reference.source_type:
            case EvidenceSource.TRANSACTION:
                return reference.reference in self.transactions
            case EvidenceSource.SETTLEMENT:
                return reference.reference in self.settlements
            case EvidenceSource.FEE_RULE:
                return reference.reference in self.fee_rules
            case EvidenceSource.POLICY_DOCUMENT:
                if reference.section is not None:
                    return (reference.reference, reference.section) in self.policy_sections
                return reference.reference in self.policy_documents
        return False  # pragma: no cover - the enum is exhaustive

    def excerpt_for(self, reference: EvidenceReference) -> str | None:
        """The retrieved text behind a policy citation, if there is one.

        Only policy evidence has an excerpt. Financial records are cited by
        identifier and can be re-read from the financial core, which remains
        their source of truth; copying their values here would create a second
        one that silently goes stale.
        """
        if reference.source_type is not EvidenceSource.POLICY_DOCUMENT:
            return None
        if reference.section is None:
            return None
        return self.policy_excerpts.get((reference.reference, reference.section))

    def ungrounded(self, references: list[EvidenceReference]) -> list[EvidenceReference]:
        """The references this ledger cannot vouch for."""
        return [reference for reference in references if not self.supports(reference)]

    def summary(self) -> str:
        """A short, log-safe description. Identifiers only, no financial detail."""
        return (
            f"transactions={sorted(self.transactions)} "
            f"settlements={sorted(self.settlements)} "
            f"fee_rules={sorted(self.fee_rules)} "
            f"policies={sorted(self.policy_documents)}"
        )
