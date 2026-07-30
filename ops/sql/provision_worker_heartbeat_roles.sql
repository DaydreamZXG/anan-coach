\set ON_ERROR_STOP on

-- DBA prerequisite for migrations 20260730_84 and 20260730_86.
-- No password or certificate is embedded here. Bind each LOGIN to a distinct
-- approved secret or client certificate out of band.
do $$
declare
  expected record;
  actual record;
begin
  for expected in
    select *
    from (
      values
        ('gongkao_worker_heartbeat', false),
        ('gongkao_worker_heartbeat_login', true),
        ('gongkao_worker_heartbeat_data_rights', false),
        ('gongkao_worker_heartbeat_data_rights_login', true),
        ('gongkao_worker_heartbeat_cause_agent', false),
        ('gongkao_worker_heartbeat_cause_agent_login', true),
        ('gongkao_worker_heartbeat_external_learning', false),
        ('gongkao_worker_heartbeat_external_learning_login', true),
        ('gongkao_worker_heartbeat_essay_review', false),
        ('gongkao_worker_heartbeat_essay_review_login', true),
        ('gongkao_worker_heartbeat_coach_notification', false),
        ('gongkao_worker_heartbeat_coach_notification_login', true),
        ('gongkao_worker_heartbeat_ilink', false),
        ('gongkao_worker_heartbeat_ilink_login', true)
    ) as required_roles(role_name, can_login)
  loop
    select *
      into actual
      from pg_roles
     where rolname = expected.role_name;
    if not found then
      if expected.can_login then
        execute format(
          'create role %I login inherit nosuperuser nocreatedb '
          'nocreaterole noreplication nobypassrls',
          expected.role_name
        );
      else
        execute format(
          'create role %I nologin inherit nosuperuser nocreatedb '
          'nocreaterole noreplication nobypassrls',
          expected.role_name
        );
      end if;
    elsif actual.rolcanlogin is distinct from expected.can_login
          or not actual.rolinherit
          or actual.rolsuper
          or actual.rolcreatedb
          or actual.rolcreaterole
          or actual.rolreplication
          or actual.rolbypassrls then
      raise exception '% exists with unsafe attributes',
        expected.role_name;
    end if;
  end loop;
end;
$$;

do $$
declare
  binding record;
  candidate_group text;
  group_roles constant text[] := array[
    'gongkao_worker_heartbeat_data_rights',
    'gongkao_worker_heartbeat_cause_agent',
    'gongkao_worker_heartbeat_external_learning',
    'gongkao_worker_heartbeat_essay_review',
    'gongkao_worker_heartbeat_coach_notification',
    'gongkao_worker_heartbeat_ilink'
  ];
begin
  -- Preserve the migration-84 prerequisite until migration 86 has revoked its
  -- runtime EXECUTE privilege.
  grant gongkao_worker_heartbeat
    to gongkao_worker_heartbeat_login;

  for binding in
    select *
    from (
      values
        (
          'gongkao_worker_heartbeat_data_rights',
          'gongkao_worker_heartbeat_data_rights_login'
        ),
        (
          'gongkao_worker_heartbeat_cause_agent',
          'gongkao_worker_heartbeat_cause_agent_login'
        ),
        (
          'gongkao_worker_heartbeat_external_learning',
          'gongkao_worker_heartbeat_external_learning_login'
        ),
        (
          'gongkao_worker_heartbeat_essay_review',
          'gongkao_worker_heartbeat_essay_review_login'
        ),
        (
          'gongkao_worker_heartbeat_coach_notification',
          'gongkao_worker_heartbeat_coach_notification_login'
        ),
        (
          'gongkao_worker_heartbeat_ilink',
          'gongkao_worker_heartbeat_ilink_login'
        )
    ) as heartbeat_bindings(group_role, login_role)
  loop
    execute format(
      'grant %I to %I',
      binding.group_role,
      binding.login_role
    );
    execute format(
      'revoke gongkao_worker_heartbeat from %I',
      binding.login_role
    );
    foreach candidate_group in array group_roles
    loop
      if candidate_group <> binding.group_role then
        execute format(
          'revoke %I from %I',
          candidate_group,
          binding.login_role
        );
      end if;
    end loop;
  end loop;

  if exists (
    select 1 from pg_roles where rolname = 'gongkao_api'
  ) then
    revoke gongkao_worker_heartbeat from gongkao_api;
    foreach candidate_group in array group_roles
    loop
      execute format(
        'revoke %I from gongkao_api',
        candidate_group
      );
    end loop;
  end if;

  foreach candidate_group in array group_roles
  loop
    execute format(
      'revoke %I from gongkao_worker_heartbeat_login',
      candidate_group
    );
    execute format(
      'revoke %I from gongkao_worker_heartbeat',
      candidate_group
    );
  end loop;
end;
$$;
