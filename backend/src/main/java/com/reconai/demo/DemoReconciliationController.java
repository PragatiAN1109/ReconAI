package com.reconai.demo;

import com.reconai.demo.dto.DemoReconcileRequest;
import com.reconai.demo.dto.DemoReconcileResponse;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * The one write operation the public demo exposes.
 *
 * <p>{@code POST} only. There is deliberately no {@code GET}, {@code PUT}, {@code PATCH}
 * or {@code DELETE} here: the endpoint creates a synthetic run and reports the verdict,
 * and nothing about the demo requires amending or removing a record afterwards. Records
 * created here are read back through the existing read endpoints like any other.
 *
 * <p>Requests are additionally bounded by {@link DemoRequestGuardFilter}, which rejects
 * oversized bodies and throttles callers before this method is reached.
 */
@RestController
@RequestMapping(DemoReconciliationController.PATH)
public class DemoReconciliationController {

    /** Also matched by {@link DemoRequestGuardFilter} and by one CloudFront behaviour. */
    public static final String PATH = "/api/v1/demo/reconcile";

    private final DemoReconciliationService demoReconciliationService;

    public DemoReconciliationController(DemoReconciliationService demoReconciliationService) {
        this.demoReconciliationService = demoReconciliationService;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public DemoReconcileResponse reconcile(@Valid @RequestBody DemoReconcileRequest request) {
        return demoReconciliationService.run(request);
    }
}
