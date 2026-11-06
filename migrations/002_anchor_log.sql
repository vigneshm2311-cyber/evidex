-- Log doctor edits to source anchors (rec/table number, page) the same way as assertions.
create or replace function source_anchor_log() returns trigger language plpgsql
set search_path = public as $$
declare who text := coalesce(nullif(current_setting('evidex.actor', true), ''), current_user);
begin
    if tg_op = 'UPDATE' then
        if to_jsonb(old) = to_jsonb(new) then return new; end if;
        insert into change_log (record_table, record_id, action, actor, before, after)
        values ('source_anchor', new.id, 'update', who, to_jsonb(old), to_jsonb(new));
    end if;
    return new;
end $$;

drop trigger if exists source_anchor_log_trg on source_anchor;
create trigger source_anchor_log_trg after update on source_anchor
    for each row execute function source_anchor_log();
