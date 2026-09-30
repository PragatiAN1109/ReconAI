// CloudFront viewer-request function.
//
// Does two jobs, in this order, on every request to the distribution:
//   1. rewrites the API prefix — /api/core/* and /api/investigation/* -> /api/v1/*
//   2. SPA history fallback — extensionless non-API paths -> /index.html
//
// Both live in ONE function because CloudFront permits only one viewer-request
// function per cache behaviour, and this function is attached to all three.
//
// WHY THE FALLBACK IS HERE AND NOT IN custom_error_response
// ---------------------------------------------------------
// A distribution's custom_error_response is distribution-WIDE: it cannot be
// scoped to a cache behaviour. Mapping 403/404 -> /index.html with a 200 there
// therefore also rewrote genuine API errors from the ALB origin, so
// `GET /api/investigation/investigations/INV-1002/recommendation` — a correct
// 404 for a FAILED investigation that has no recommendation — reached the
// browser as 200 text/html and broke JSON parsing in the console.
//
// nginx.conf gets this right for free: `try_files $uri $uri/ /index.html` sits
// in `location /`, so the two `location /api/...` prefixes never see it. This
// function is the CloudFront equivalent of that scoping, which is why the bug
// reproduced only on AWS and never in Vite or Compose.
//
// The demo is deliberately open: there is no gate here. It serves synthetic
// data only, and an access prompt defeated the point of publishing it.
//
// The function never touches the request body. CloudFront Functions have no
// body access at all, so POST payloads pass through unmodified by construction
// rather than by care. Query strings live on request.querystring, which is left
// alone; only request.uri is rewritten.

function handler(event) {
  var request = event.request;
  var uri = request.uri;

  // --- 1. API: rewrite the prefix, then hand straight to the origin --------
  //
  // Both backends serve under /api/v1. The two browser-facing prefixes exist
  // to tell the services apart at the edge; they are collapsed here so no
  // backend source change is needed for AWS routing.
  //
  // Every /api/ path returns early. An API response — including a 404, a 409
  // or a 503 — must reach the client as itself, never as the SPA shell.
  if (uri.indexOf("/api/") === 0) {
    if (uri.indexOf("/api/core/") === 0) {
      request.uri = "/api/v1/" + uri.substring("/api/core/".length);
    } else if (uri.indexOf("/api/investigation/") === 0) {
      request.uri = "/api/v1/" + uri.substring("/api/investigation/".length);
    }
    return request;
  }

  // --- 2. SPA history fallback --------------------------------------------
  //
  // /investigations/INV-1002 is a client-side route with no object behind it.
  // Without this, S3 answers 403 through OAC and a refresh or a pasted link
  // would fail.
  //
  // The test is "does the last path segment carry an extension": /assets/*.js,
  // *.css, favicons and source maps are real objects and are left alone, so a
  // genuinely missing asset still fails as a missing asset instead of being
  // masked by an HTML document with a 200.
  var lastSegment = uri.substring(uri.lastIndexOf("/") + 1);
  if (lastSegment.indexOf(".") === -1) {
    request.uri = "/index.html";
  }

  return request;
}
