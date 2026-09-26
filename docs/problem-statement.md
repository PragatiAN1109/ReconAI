# ReconAI — Problem Statement

## 1. Overview

Financial institutions and payment platforms process transactions across multiple systems, including authorization platforms, internal transaction stores, payment processors, settlement systems, merchant configurations, and financial ledgers.

A transaction that is successfully processed by one system does not necessarily reconcile correctly with the records produced by another.

For example, an internal transaction system may expect a settlement of **$1,247.50**, while the payment processor reports a settlement of **$1,217.50**.

The $30 difference can be detected programmatically. Determining **why** that difference exists, however, may require investigating several independent sources of information.

An operations analyst may need to:

1. Retrieve the original transaction.
2. Retrieve the corresponding settlement record.
3. Compare transaction and settlement attributes.
4. Inspect merchant or processor fee configurations.
5. Review relevant settlement policies.
6. Examine historical transactions for similar behavior.
7. Determine the likely root cause.
8. Document supporting evidence.
9. Recommend an appropriate resolution.
10. Escalate the exception when the available evidence is insufficient.

This makes reconciliation exception investigation a time-consuming operational workflow, particularly when relevant information is distributed across multiple systems.

ReconAI explores how this investigation process can be accelerated using an AI-assisted workflow without allowing probabilistic AI behavior to determine financial correctness or autonomously modify financial records.

---

## 2. Problem

Traditional reconciliation systems are effective at identifying deterministic discrepancies such as:

- mismatched transaction amounts;
- missing settlement records;
- duplicate settlements;
- currency mismatches; and
- other violations of predefined reconciliation rules.

Detection alone does not necessarily explain the underlying business reason for a discrepancy.

Consider the following example:

### Internal Transaction

```text
Transaction ID: TX-48291
Merchant: MERCHANT-104
Expected Settlement: $1,247.50
Currency: USD
```

### Processor Settlement

```text
Settlement ID: SET-8821
Transaction ID: TX-48291
Settled Amount: $1,217.50
Currency: USD
```

A deterministic reconciliation engine can establish:

```text
Exception Type: AMOUNT_MISMATCH
Expected Amount: $1,247.50
Actual Amount:   $1,217.50
Difference:      $30.00
```

This conclusion is deterministic and can be derived directly from authoritative financial records.

However, the reconciliation engine cannot conclude solely from these records that:

```text
The $30 discrepancy was caused by processor fee FR-14.
```

Establishing that explanation may require additional evidence such as:

- the merchant's configured fee rules;
- processor settlement policies;
- previous settlements for the merchant;
- transaction metadata; and
- operational reconciliation procedures.

The distinction between **detecting a discrepancy** and **investigating its cause** is central to ReconAI.

---

## 3. Detection Is Not Investigation

ReconAI deliberately separates two responsibilities.

### Detection

Detection answers:

> **What is inconsistent between the authoritative financial records?**

Detection must be deterministic.

For example:

```text
Expected = $1,247.50
Actual   = $1,217.50

Difference = $30.00

Result = AMOUNT_MISMATCH
```

The same input must always produce the same reconciliation result.

An LLM is therefore **not involved in determining whether transactions reconcile**.

### Investigation

Investigation answers:

> **Why did this discrepancy occur, and what evidence supports that explanation?**

An investigation may require dynamically gathering information from multiple sources.

For example:

```text
AMOUNT_MISMATCH
        |
        v
Retrieve transaction
        |
        v
Retrieve settlement
        |
        v
Inspect merchant fee configuration
        |
        v
Find applicable $30 processor fee
        |
        v
Retrieve relevant settlement policy
        |
        v
Compare previous merchant settlements
        |
        v
Generate evidence-backed explanation
```

Different exceptions may require different investigation paths.

ReconAI therefore uses an AI agent to assist with the investigation layer while keeping the underlying reconciliation process deterministic.

---

## 4. Core Design Principle

ReconAI follows one primary architectural principle:

> **Deterministic systems detect. AI investigates. Humans authorize.**

Each layer has a different responsibility.

### Deterministic Systems Detect

Financial discrepancies are identified using explicit reconciliation rules operating on authoritative financial data.

AI does not determine whether money is missing, duplicated, incorrectly settled, or denominated in the wrong currency.

### AI Investigates

Once an exception has been identified, an AI investigation agent may gather information from approved systems and determine a likely explanation.

The agent must support its conclusions using retrieved evidence.

### Humans Authorize

AI-generated recommendations are advisory.

The agent cannot:

- modify transaction records;
- modify settlement records;
- change ledger balances;
- approve its own recommendations;
- initiate money movement; or
- mark a financial discrepancy as resolved without an authorized human decision.

Consequential financial actions remain outside the agent's authority.

---

## 5. Target User

The primary user of ReconAI is a **financial operations or reconciliation analyst** responsible for investigating payment discrepancies.

Today, an analyst may manually move between transaction systems, settlement records, merchant configurations, internal documentation, and historical records.

ReconAI aims to consolidate the investigation process into an evidence-backed workflow.

Instead of manually searching each source, the analyst receives:

```text
Detected Exception
        +
Likely Root Cause
        +
Supporting Evidence
        +
Relevant Policies
        +
Recommended Action
        +
Confidence / Escalation
```

The analyst remains responsible for accepting, rejecting, or escalating the recommendation.

---

## 6. Core Domain Entities

### Transaction

Represents the internal record of a financial transaction.

Typical attributes include:

- transaction ID;
- merchant ID;
- transaction amount;
- expected settlement amount;
- currency;
- transaction timestamp; and
- transaction status.

---

### Settlement

Represents the settlement record received from a processor or external financial system.

Typical attributes include:

- settlement ID;
- transaction ID;
- settled amount;
- settlement currency;
- processor;
- settlement timestamp; and
- settlement status.

---

### ReconciliationException

Represents a deterministic discrepancy discovered while comparing financial records.

It contains information such as:

- exception ID;
- transaction ID;
- exception type;
- expected value;
- observed value;
- difference;
- detection timestamp; and
- exception status.

A `ReconciliationException` represents **what went wrong**, not necessarily **why it happened**.

---

### Investigation

Represents the AI-assisted investigation associated with a reconciliation exception.

It tracks:

- investigation ID;
- exception ID;
- investigation status;
- start and completion timestamps;
- tools used;
- confidence;
- and investigation outcome.

---

### InvestigationEvidence

Represents authoritative information retrieved during an investigation.

Evidence may originate from:

- transaction records;
- settlement records;
- merchant configurations;
- fee rules;
- historical transactions; or
- policy documents.

Every significant investigation conclusion should be traceable to supporting evidence.

---

### Recommendation

Represents the investigation agent's proposed explanation and next action.

A recommendation may contain:

- root-cause classification;
- root-cause explanation;
- supporting evidence references;
- confidence;
- recommended action; and
- whether escalation is required.

A recommendation does not itself modify financial state.

---

### Approval

Represents a human decision regarding an AI-generated recommendation.

Possible outcomes include:

```text
APPROVED
REJECTED
ESCALATED
```

The approval records the responsible user and timestamp.

---

### AuditEvent

Represents an immutable record of a significant action performed during reconciliation or investigation.

Examples include:

```text
EXCEPTION_DETECTED
INVESTIGATION_STARTED
TOOL_CALLED
EVIDENCE_RETRIEVED
RECOMMENDATION_GENERATED
INVESTIGATION_ESCALATED
RECOMMENDATION_APPROVED
RECOMMENDATION_REJECTED
```

Audit events make the investigation process reconstructable.

---

### PolicyDocument

Represents operational or financial documentation that may provide context during an investigation.

Examples include:

- merchant fee schedules;
- settlement policies;
- reconciliation procedures;
- currency conversion policies; and
- exception-handling guidelines.

Policy documents form the knowledge base used by the retrieval system.

---

## 7. Initial Reconciliation Exception Types

ReconAI V1 supports five primary exception scenarios.

### 7.1 AMOUNT_MISMATCH

The expected settlement amount differs from the observed settlement amount.

Example:

```text
Expected: $1,247.50
Actual:   $1,217.50
Difference: $30.00
```

Possible causes may include fees, adjustments, incorrect processor data, or other settlement behavior.

The deterministic system detects the mismatch.

The investigation layer attempts to determine its cause.

---

### 7.2 MISSING_SETTLEMENT

An internal transaction exists but no corresponding settlement record can be located within the expected settlement window.

Example:

```text
Transaction: TX-92811
Expected settlement: Present
Processor settlement: Not found
```

The investigation may examine settlement timing rules, processor records, transaction status, and applicable operational policies.

---

### 7.3 DUPLICATE_SETTLEMENT

Multiple settlement records appear to correspond to the same transaction when only one settlement is expected.

Example:

```text
Transaction: TX-88217

Settlement:
SET-10031 — $850.00

Settlement:
SET-10094 — $850.00
```

The deterministic engine identifies the duplicate relationship.

The investigation determines whether it represents an actual duplicate payment, correction, reversal, retry, or another supported processor behavior.

---

### 7.4 PROCESSOR_FEE

A settlement discrepancy may ultimately be explained by an applicable processor or merchant fee.

For example:

```text
Expected settlement: $1,247.50
Actual settlement:   $1,217.50

Difference: $30.00

Applicable rule:
FR-14 — $30 processor settlement fee
```

Importantly, `PROCESSOR_FEE` may begin as an `AMOUNT_MISMATCH`.

The deterministic engine detects the $30 difference.

The investigation agent determines that the available evidence supports `PROCESSOR_FEE` as the root-cause classification.

This distinction prevents business explanations from being embedded prematurely into deterministic reconciliation logic.

---

### 7.5 CURRENCY_MISMATCH

The transaction and settlement records use inconsistent currencies.

Example:

```text
Transaction currency: USD
Settlement currency: EUR
```

The deterministic engine identifies the inconsistency.

The investigation may then determine whether currency conversion was expected, a processor rule applies, or the settlement record is incorrect.

---

## 8. Investigation Requirements

For each reconciliation exception, ReconAI should be capable of gathering information through explicitly authorized tools.

Initial investigation capabilities include:

```text
get_transaction(transaction_id)

get_settlement(transaction_id)

get_fee_rules(merchant_id)

get_transaction_history(merchant_id)

search_policy_documents(query)
```

The agent should not have unrestricted database access.

Each tool represents a controlled interface to an authoritative information source.

The investigation result must contain:

```text
Root-cause classification

Root-cause explanation

Confidence

Supporting evidence

Recommended action

Human-approval requirement

Escalation status
```

If sufficient evidence cannot be obtained, the system should prefer escalation over generating an unsupported explanation.

---

## 9. Safety and Control Requirements

Because ReconAI operates in a financial-services context, AI functionality must operate within explicit boundaries.

### No Autonomous Financial Actions

The AI system must not modify authoritative financial records or initiate financial transactions.

### Human-in-the-Loop

Consequential recommendations require human review.

### Evidence Grounding

Material conclusions must reference evidence retrieved from approved sources.

### Restricted Tool Access

The agent may interact only with explicitly provided tools.

### Data Minimization

Only information required for an investigation should be provided to the model.

Sensitive customer information should be excluded or redacted where it is unnecessary.

### Escalation

The system must support an `INSUFFICIENT_EVIDENCE` or equivalent outcome rather than forcing the agent to select a root cause.

### Auditability

Investigation activity and human decisions must be recorded so the workflow can later be reconstructed.

---

## 10. Success Criteria for V1

ReconAI V1 will be considered successful when the following workflow operates end-to-end:

```text
Transaction + Settlement
        |
        v
Deterministic Reconciliation
        |
        v
Exception Detected
        |
        v
Asynchronous Investigation Triggered
        |
        v
Agent Retrieves Evidence Through Tools
        |
        v
Relevant Policies Retrieved
        |
        v
Evidence-backed Recommendation Generated
        |
        v
Human Reviews Recommendation
        |
        +---- Approve
        |
        +---- Reject
        |
        +---- Escalate
        |
        v
Decision Recorded in Audit Trail
```

For the primary demonstration scenario:

```text
Expected Settlement: $1,247.50

Actual Settlement: $1,217.50

Detected Exception:
AMOUNT_MISMATCH

Difference:
$30.00
```

The investigation should independently discover evidence indicating that an applicable processor fee explains the difference and produce a recommendation referencing that evidence.

The human reviewer must remain responsible for the final decision.

---

## 11. Non-Goals for V1

ReconAI V1 is not intended to:

- process real customer financial data;
- connect to production banking systems;
- execute payments;
- modify financial ledgers;
- autonomously resolve financial exceptions;
- replace reconciliation analysts;
- provide regulatory or financial advice;
- model every possible reconciliation scenario; or
- represent a production-ready banking platform.

All V1 financial data and policies are synthetic.

The objective is to demonstrate the architecture and engineering patterns required for safe AI-assisted investigation of financial workflows.

---

## 12. Longer-Term Direction

A production-oriented implementation could extend ReconAI with:

- integrations with real processor and banking APIs;
- event-driven transaction ingestion;
- enterprise identity and role-based access control;
- stronger PII/tokenization controls;
- configurable reconciliation rules;
- dead-letter queues and replay;
- idempotent event processing;
- distributed tracing;
- model and prompt versioning;
- expanded evaluation datasets;
- investigation-quality monitoring;
- analyst feedback loops;
- policy versioning;
- multi-tenant support; and
- production-scale cloud infrastructure.

These capabilities are intentionally outside the initial vertical slice.

---

## 13. Engineering Thesis

ReconAI is based on the premise that introducing AI into financial systems should not require replacing deterministic financial controls with probabilistic decision-making.

Instead, AI can be placed where uncertainty already exists: **investigation, information gathering, evidence synthesis, and operational assistance.**

The financial system remains responsible for establishing facts.

The AI system assists in understanding those facts.

The human remains responsible for consequential decisions.

> **Deterministic systems detect. AI investigates. Humans authorize.**