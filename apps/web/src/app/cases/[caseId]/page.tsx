"use client";

/**
 * One case: what was asked for, how the system decided, what it checked, and the
 * clinician's own determination.
 *
 * The order on the page is the order a reviewer needs it in -- the request and the
 * outcome, then the disclosure of what a model did and did not do, then the machine's
 * working, then the policy criterion by criterion, and the form last. The form is last
 * because a determination should be the thing you reach after reading the evidence, not
 * the thing you scroll past it to find.
 */

import { useCallback, useEffect, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

import { AppShell } from "@/components/AppShell";
import { CaseSummary } from "@/components/CaseSummary";
import { CriteriaSets } from "@/components/CriteriaSets";
import { DisclosureDetail } from "@/components/AiDisclosure";
import { ReviewForm } from "@/components/ReviewForm";
import { ReviewHistory } from "@/components/ReviewHistory";
import { StepStream } from "@/components/StepStream";
import { useSession } from "@/components/SessionProvider";
import { useCaseEvents } from "@/hooks/useCaseEvents";
import { useResource } from "@/hooks/useResource";
import * as api from "@/lib/api";
import { determinationFrom } from "@/lib/determination";
import { mayReview } from "@/lib/session";
import type { CaseCriteria, Case, Review } from "@/lib/types";

export default function CaseDetailPage() {
  const params = useParams<{ caseId: string }>();
  const caseId = params.caseId;
  const { session } = useSession();

  const loadCase = useCallback(
    (token: string, signal: AbortSignal) => api.getCase(token, caseId, signal),
    [caseId],
  );
  const loadCriteria = useCallback(
    (token: string, signal: AbortSignal) => api.getCaseCriteria(token, caseId, signal),
    [caseId],
  );
  const loadReviews = useCallback(
    (token: string, signal: AbortSignal) => api.listCaseReviews(token, caseId, signal),
    [caseId],
  );

  const caseResource = useResource<Case>(loadCase);
  const criteriaResource = useResource<CaseCriteria>(loadCriteria);
  const reviewsResource = useResource<Review[]>(loadReviews);

  const caseData = caseResource.data;
  // Only a case still moving is worth an open connection. `decided` and `failed` are
  // terminal for this run, and the replayed log already holds everything they produced.
  const live = caseData !== null && (caseData.status === "queued" || caseData.status === "running");

  const { events, error: eventsError, streamError } = useCaseEvents(caseId, live);
  const determination = useMemo(() => determinationFrom(events), [events]);
  const decidedAt = determination?.decided_at ?? null;

  // A decision arriving over the live stream is the moment the criteria and the case's
  // own status become worth re-reading -- neither exists in a useful form before it.
  // Guarded on `live`, which the reload below flips to false, so this settles after one
  // pass rather than looping.
  const reloadCase = caseResource.reload;
  const reloadCriteria = criteriaResource.reload;
  useEffect(() => {
    if (!live || decidedAt === null) return;
    reloadCase();
    reloadCriteria();
  }, [live, decidedAt, reloadCase, reloadCriteria]);

  const criterionIds = useMemo(
    () =>
      new Set(
        (criteriaResource.data?.sets ?? []).flatMap((set) =>
          set.criteria.map((criterion) => criterion.id),
        ),
      ),
    [criteriaResource.data],
  );

  const canReview = session !== null && mayReview(session.user.role);

  return (
    <AppShell>
      <div className="stack">
        <Link href="/cases" className="small">
          &larr; Back to the queue
        </Link>

        {caseResource.error ? <p className="error">{caseResource.error}</p> : null}

        {caseData === null ? (
          <p className="notice">Loading case&hellip;</p>
        ) : (
          <>
            <CaseSummary
              caseData={caseData}
              determination={determination}
              criterionIds={criterionIds}
            />

            <DisclosureDetail />

            <StepStream
              events={events}
              live={live}
              error={eventsError}
              streamError={streamError}
            />

            <section className="stack stack--tight">
              <h2>What the policy requires</h2>
              {criteriaResource.error ? (
                <p className="error">{criteriaResource.error}</p>
              ) : criteriaResource.data === null ? (
                <p className="notice">Loading criteria&hellip;</p>
              ) : (
                <CriteriaSets
                  criteria={criteriaResource.data}
                  determination={determination}
                />
              )}
            </section>

            <ReviewHistory reviews={reviewsResource.data ?? []} />

            {canReview ? (
              <ReviewForm caseId={caseId} onRecorded={reviewsResource.reload} />
            ) : (
              /* The gateway permits POST /api/cases/{id}/review to a clinician and an
                 admin only -- not to a reviewer. That is a legal distinction rather than
                 a hierarchy: only a clinical peer may issue an adverse determination.
                 Saying so beats rendering a form that would be refused. */
              <p className="notice">
                Recording a determination on a case is a clinician&rsquo;s act. Your
                account may read this case but not decide it.
              </p>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
