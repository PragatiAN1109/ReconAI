/**
 * Calls against the Spring Financial Core.
 *
 * Read-only, and that is the whole contract. There is no create, update or
 * delete here and there must never be: the console exists to investigate and
 * review, and the Investigation Service itself issues no write to this service
 * either.
 */

import type {
  ReconciliationException,
  Transaction,
  TransactionSettlements,
} from "../types";
import { CORE_BASE, getOptional } from "./client";

/** Null when the exception is not in the Financial Core. */
export function getException(
  exceptionId: string,
): Promise<ReconciliationException | null> {
  return getOptional<ReconciliationException>(
    `${CORE_BASE}/exceptions/${exceptionId}`,
  );
}

export function getTransaction(
  transactionId: string,
): Promise<Transaction | null> {
  return getOptional<Transaction>(`${CORE_BASE}/transactions/${transactionId}`);
}

export function getSettlements(
  transactionId: string,
): Promise<TransactionSettlements | null> {
  return getOptional<TransactionSettlements>(
    `${CORE_BASE}/transactions/${transactionId}/settlements`,
  );
}
