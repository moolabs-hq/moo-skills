import express from "express";

const app = express();
app.get("/emit", handler);

const thread_id = currentThreadId();
propagateThreadId(thread_id);
publish(
  "missing",
  payload,
);
publish(
  "verified",
  injectThreadId(payload, { thread_id }),
);
/* publish("ghost", injectThreadId(payload, { thread_id })); */
