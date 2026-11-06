-- Audit log of every agent (LLM) call: exactly what it saw and what it returned.
create table if not exists agent_call (
    id        bigserial primary key,
    run_id    text not null,
    step      text not null,          -- scope_guard / planner / synthesizer:<section>
    model     text not null,
    input     jsonb not null,         -- system prompt + user content sent to the model
    output    jsonb,                  -- parsed JSON reply (null on failure)
    error     text,
    attempts  int not null default 1,
    ms        int,
    at        timestamptz not null default now()
);
create index if not exists agent_call_run_idx on agent_call (run_id);
alter table agent_call enable row level security;
