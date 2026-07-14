import express from "express";

const app = express();
const literal = "// app.get('/string-ghost', handler)";
const blockLiteral = "/* app.use(AttributionMiddleware) */";

// app.get("/line-ghost", handler);
/*
app.use(AttributionMiddleware);
app.post("/block-ghost", handler);
*/

app.use(
  AttributionMiddleware,
);

app.get(
  "/literal//path",
  handler,
);

void literal;
void blockLiteral;
