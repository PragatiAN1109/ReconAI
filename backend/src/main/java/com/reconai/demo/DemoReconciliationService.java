package com.reconai.demo;

import com.reconai.demo.dto.DemoReconcileRequest;
import com.reconai.demo.dto.DemoReconcileResponse;
import com.reconai.demo.dto.DemoSettlementInput;
import com.reconai.reconciliation.ReconciliationService;
import com.reconai.reconciliation.dto.ReconciliationResponse;
import com.reconai.settlement.Settlement;
import com.reconai.settlement.SettlementService;
import com.reconai.settlement.SettlementStatus;
import com.reconai.settlement.dto.CreateSettlementRequest;
import com.reconai.transaction.Transaction;
import com.reconai.transaction.TransactionService;
import com.reconai.transaction.TransactionStatus;
import com.reconai.transaction.TransactionType;
import com.reconai.transaction.dto.CreateTransactionRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

/**
 * Creates a synthetic transaction and its settlements, then reconciles them.
 *
 * <h2>This is a facade and nothing more</h2>
 *
 * Every step delegates to the service that already owns it —
 * {@link TransactionService}, {@link SettlementService}, {@link ReconciliationService}.
 * No rule is reimplemented here and no rule is bypassed. The records this creates are
 * ordinary records, produced by the ordinary code paths with ordinary sequence-generated
 * identifiers, so a demo run exercises the real engine rather than a parallel one.
 *
 * <p>It exists because the alternative — letting a public caller post transactions,
 * settlements and reconciliation runs through the generic endpoints — would mean exposing
 * three unrestricted financial write APIs to the internet. This is one operation with one
 * validated shape.
 *
 * <h2>What the caller does not control</h2>
 *
 * Identifiers, merchant, processor, transaction type, both statuses and both timestamps.
 * A caller supplies amounts and currencies; the server supplies identity. That is what
 * keeps this from being a general-purpose record-creation API.
 *
 * <p>Settlement status is forced to {@code COMPLETED} in particular: reconciliation
 * considers only completed settlements, so any other value would report a missing
 * settlement for one the response shows as present.
 *
 * <h2>Transaction boundary</h2>
 *
 * One transaction covers creation and reconciliation, so a failure leaves nothing behind.
 * The exception event is published by
 * {@link com.reconai.messaging.ReconciliationExceptionPublisher} on
 * {@code AFTER_COMMIT}, which means it fires once this whole method has committed —
 * never for a run that rolled back.
 *
 * <h2>No AI</h2>
 *
 * Nothing here calls, knows about or depends on the Investigation Service. A demo run
 * produces a deterministic verdict and, when that verdict is a discrepancy, a Kafka
 * event. Whether anything investigates it is a separate, human-initiated decision.
 */
@Service
public class DemoReconciliationService {

    private static final Logger log = LoggerFactory.getLogger(DemoReconciliationService.class);

    /**
     * Server-forced transaction type. A demo run is always a purchase: refunds and
     * reversals carry semantics the playground does not model.
     */
    private static final TransactionType DEMO_TRANSACTION_TYPE = TransactionType.PURCHASE;

    /**
     * Server-forced transaction status. {@code POSTED} is a transaction recorded and
     * awaiting settlement, which is the state reconciliation is about. {@code SETTLED}
     * would contradict the missing-settlement scenario.
     */
    private static final TransactionStatus DEMO_TRANSACTION_STATUS = TransactionStatus.POSTED;

    /** Server-forced settlement status. See the class comment. */
    private static final SettlementStatus DEMO_SETTLEMENT_STATUS = SettlementStatus.COMPLETED;

    private final TransactionService transactionService;
    private final SettlementService settlementService;
    private final ReconciliationService reconciliationService;
    private final DemoProperties properties;
    private final Clock clock;

    public DemoReconciliationService(TransactionService transactionService,
                                     SettlementService settlementService,
                                     ReconciliationService reconciliationService,
                                     DemoProperties properties,
                                     Clock clock) {
        this.transactionService = transactionService;
        this.settlementService = settlementService;
        this.reconciliationService = reconciliationService;
        this.properties = properties;
        this.clock = clock;
    }

    @Transactional
    public DemoReconcileResponse run(DemoReconcileRequest request) {
        Instant now = Instant.now(clock);

        Transaction transaction = transactionService.create(new CreateTransactionRequest(
                properties.merchantId(),
                request.transaction().amount(),
                request.transaction().expectedSettlementAmount(),
                request.transaction().currency(),
                DEMO_TRANSACTION_TYPE,
                DEMO_TRANSACTION_STATUS,
                now));

        List<String> settlementIds = new ArrayList<>();
        for (DemoSettlementInput input : request.settlementsOrEmpty()) {
            Settlement settlement = settlementService.create(new CreateSettlementRequest(
                    transaction.getTransactionId(),
                    properties.processor(),
                    input.settledAmount(),
                    input.currency(),
                    DEMO_SETTLEMENT_STATUS,
                    now));
            settlementIds.add(settlement.getSettlementId());
        }

        ReconciliationResponse reconciliation = ReconciliationResponse.from(
                reconciliationService.reconcile(transaction.getTransactionId()));

        log.info("Demo reconciliation run [transactionId={} settlements={} reconciled={}]",
                transaction.getTransactionId(), settlementIds.size(), reconciliation.reconciled());

        return new DemoReconcileResponse(
                transaction.getTransactionId(), List.copyOf(settlementIds), reconciliation);
    }
}
