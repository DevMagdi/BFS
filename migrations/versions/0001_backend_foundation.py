"""Initial secure tenant, identity, session, and audit schema.

Revision ID: 0001_backend_foundation
Revises:
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0001_backend_foundation"
down_revision = None
branch_labels = None
depends_on = None


RUNTIME_ROLE = "bf_runtime"


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bf_runtime') THEN
            RAISE EXCEPTION 'required non-privileged role bf_runtime does not exist';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bf_migrator') THEN
            RAISE EXCEPTION 'required migration role bf_migrator does not exist';
          END IF;
          IF current_user <> 'bf_migrator' THEN
            RAISE EXCEPTION 'migrations must run as the dedicated bf_migrator role';
          END IF;
          IF (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'bf_runtime') THEN
            RAISE EXCEPTION 'bf_runtime must not be a superuser or bypass RLS';
          END IF;
          IF pg_has_role('bf_runtime', 'bf_migrator', 'MEMBER') THEN
            RAISE EXCEPTION 'bf_runtime must not inherit or assume the migration role';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_database
            WHERE datname = current_database()
              AND datdba = (SELECT oid FROM pg_roles WHERE rolname = 'bf_runtime')
          ) THEN
            RAISE EXCEPTION 'bf_runtime must not own the application database';
          END IF;
          IF has_schema_privilege('bf_runtime', 'public', 'CREATE') THEN
            RAISE EXCEPTION 'bf_runtime must not create objects in the application schema';
          END IF;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE TABLE users (
          id uuid PRIMARY KEY,
          email varchar(320) NOT NULL,
          display_name varchar(120) NOT NULL,
          password_hash varchar(512) NOT NULL,
          status varchar(16) NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_users_email UNIQUE (email),
          CONSTRAINT ck_users_status CHECK (status IN ('active', 'disabled')),
          CONSTRAINT ck_users_email_length CHECK (length(email) BETWEEN 3 AND 320),
          CONSTRAINT ck_users_display_name_length CHECK (length(display_name) BETWEEN 1 AND 120)
        );

        CREATE TABLE tenants (
          id uuid PRIMARY KEY,
          name varchar(160) NOT NULL,
          primary_company_id uuid NOT NULL,
          status varchar(16) NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_tenants_status CHECK (status IN ('active', 'disabled')),
          CONSTRAINT ck_tenants_name_length CHECK (length(name) BETWEEN 1 AND 160)
        );

        CREATE TABLE companies (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL,
          legal_name varchar(200) NOT NULL,
          base_currency varchar(3) NOT NULL,
          timezone varchar(64) NOT NULL DEFAULT 'UTC',
          locale varchar(16) NOT NULL DEFAULT 'en',
          status varchar(16) NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_companies_one_per_tenant UNIQUE (tenant_id),
          CONSTRAINT uq_companies_tenant_id_id UNIQUE (tenant_id, id),
          CONSTRAINT fk_companies_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT,
          CONSTRAINT ck_companies_status CHECK (status IN ('active', 'disabled')),
          CONSTRAINT ck_companies_currency CHECK (base_currency ~ '^[A-Z]{3}$'),
          CONSTRAINT ck_companies_legal_name CHECK (length(legal_name) BETWEEN 1 AND 200)
        );

        ALTER TABLE tenants
          ADD CONSTRAINT fk_tenants_primary_company
          FOREIGN KEY (id, primary_company_id)
          REFERENCES companies (tenant_id, id)
          DEFERRABLE INITIALLY DEFERRED;

        CREATE TABLE tenant_memberships (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL,
          user_id uuid NOT NULL,
          role varchar(16) NOT NULL,
          status varchar(16) NOT NULL DEFAULT 'active',
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_memberships_tenant_user UNIQUE (tenant_id, user_id),
          CONSTRAINT uq_memberships_tenant_id_id UNIQUE (tenant_id, id),
          CONSTRAINT fk_memberships_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT,
          CONSTRAINT fk_memberships_user
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
          CONSTRAINT ck_memberships_role CHECK (role IN ('owner', 'admin', 'member')),
          CONSTRAINT ck_memberships_status CHECK (status IN ('active', 'revoked', 'suspended')),
          CONSTRAINT ck_membership_revocation CHECK (
            (status = 'revoked' AND revoked_at IS NOT NULL) OR
            (status <> 'revoked' AND revoked_at IS NULL)
          )
        );
        CREATE INDEX ix_memberships_user_status ON tenant_memberships (user_id, status);
        CREATE INDEX ix_memberships_tenant_role_status
          ON tenant_memberships (tenant_id, role, status);

        CREATE TABLE auth_sessions (
          id uuid PRIMARY KEY,
          user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          last_seen_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          revoke_reason varchar(40),
          CONSTRAINT fk_auth_sessions_user
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
          CONSTRAINT ck_auth_sessions_expiry CHECK (expires_at > created_at),
          CONSTRAINT ck_auth_sessions_revocation CHECK (
            (revoked_at IS NULL AND revoke_reason IS NULL) OR
            (revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)
          )
        );
        CREATE INDEX ix_auth_sessions_user_active
          ON auth_sessions (user_id, revoked_at, expires_at);

        CREATE TABLE refresh_tokens (
          id uuid PRIMARY KEY,
          session_id uuid NOT NULL,
          token_hash varchar(64) NOT NULL,
          generation integer NOT NULL,
          issued_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          consumed_at timestamptz,
          revoked_at timestamptz,
          replaced_by_id uuid,
          CONSTRAINT uq_refresh_tokens_hash UNIQUE (token_hash),
          CONSTRAINT uq_refresh_tokens_session_generation UNIQUE (session_id, generation),
          CONSTRAINT fk_refresh_tokens_session
            FOREIGN KEY (session_id) REFERENCES auth_sessions(id) ON DELETE RESTRICT,
          CONSTRAINT fk_refresh_tokens_replaced_by
            FOREIGN KEY (replaced_by_id) REFERENCES refresh_tokens(id) ON DELETE RESTRICT,
          CONSTRAINT ck_refresh_tokens_generation CHECK (generation >= 0),
          CONSTRAINT ck_refresh_tokens_hash CHECK (token_hash ~ '^[0-9a-f]{64}$'),
          CONSTRAINT ck_refresh_tokens_state CHECK (consumed_at IS NULL OR consumed_at >= issued_at)
        );
        CREATE INDEX ix_refresh_tokens_session ON refresh_tokens (session_id);

        CREATE TABLE login_throttles (
          bucket_hash varchar(64) PRIMARY KEY,
          failure_count integer NOT NULL,
          window_started_at timestamptz NOT NULL,
          locked_until timestamptz,
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_login_throttles_failure_count CHECK (failure_count >= 0),
          CONSTRAINT ck_login_throttles_hash CHECK (bucket_hash ~ '^[0-9a-f]{64}$')
        );

        CREATE TABLE tenant_onboarding_requests (
          user_id uuid NOT NULL,
          key_hash varchar(64) NOT NULL,
          request_hash varchar(64) NOT NULL,
          tenant_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_tenant_onboarding_requests PRIMARY KEY (user_id, key_hash),
          CONSTRAINT uq_onboarding_tenant UNIQUE (tenant_id),
          CONSTRAINT fk_onboarding_user
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
          CONSTRAINT fk_onboarding_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_onboarding_hash CHECK (key_hash ~ '^[0-9a-f]{64}$'),
          CONSTRAINT ck_onboarding_request_hash CHECK (request_hash ~ '^[0-9a-f]{64}$')
        );
        CREATE INDEX ix_onboarding_tenant ON tenant_onboarding_requests (tenant_id);

        CREATE TABLE audit_events (
          id uuid PRIMARY KEY,
          tenant_id uuid,
          actor_user_id uuid,
          event_type varchar(80) NOT NULL,
          target_type varchar(40),
          target_id uuid,
          request_id uuid,
          outcome varchar(16) NOT NULL DEFAULT 'success',
          metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT fk_audit_events_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT,
          CONSTRAINT fk_audit_events_actor
            FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE RESTRICT,
          CONSTRAINT ck_audit_events_type_length CHECK (length(event_type) BETWEEN 1 AND 80),
          CONSTRAINT ck_audit_events_outcome
            CHECK (outcome IN ('success', 'failure', 'denied', 'revoked')),
          CONSTRAINT ck_audit_events_metadata_object CHECK (jsonb_typeof(metadata) = 'object')
        );
        CREATE INDEX ix_audit_events_tenant_created ON audit_events (tenant_id, created_at, id);
        CREATE INDEX ix_audit_events_actor_created ON audit_events (actor_user_id, created_at);
        """
    )

    op.execute(
        """
        CREATE FUNCTION businessflow_require_active_owner() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE checked_tenant uuid;
        BEGIN
          IF TG_TABLE_NAME = 'tenants' THEN
            IF TG_OP = 'DELETE' THEN
              checked_tenant := OLD.id;
            ELSE
              checked_tenant := NEW.id;
            END IF;
          ELSIF TG_OP = 'DELETE' THEN
            checked_tenant := OLD.tenant_id;
          ELSE
            checked_tenant := NEW.tenant_id;
          END IF;
          IF EXISTS (
            SELECT 1 FROM tenants WHERE id = checked_tenant AND status = 'active'
          ) AND NOT EXISTS (
            SELECT 1 FROM tenant_memberships
            WHERE tenant_id = checked_tenant AND role = 'owner' AND status = 'active'
          ) THEN
            RAISE EXCEPTION 'active tenant must retain an active owner'
              USING ERRCODE = '23514', CONSTRAINT = 'tenant_requires_active_owner';
          END IF;
          RETURN NULL;
        END;
        $$;
        CREATE CONSTRAINT TRIGGER trg_tenant_requires_active_owner
          AFTER INSERT OR UPDATE OR DELETE ON tenant_memberships
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION businessflow_require_active_owner();
        CREATE CONSTRAINT TRIGGER trg_tenants_require_active_owner
          AFTER INSERT OR UPDATE ON tenants
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION businessflow_require_active_owner();

        CREATE FUNCTION businessflow_audit_is_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit events are append-only'
            USING ERRCODE = '55000', CONSTRAINT = 'audit_events_immutable';
        END;
        $$;
        CREATE TRIGGER trg_audit_events_immutable
          BEFORE UPDATE OR DELETE ON audit_events
          FOR EACH ROW EXECUTE FUNCTION businessflow_audit_is_immutable();
        """
    )

    op.execute(
        """
        ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_context ON tenants
          USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

        ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
        ALTER TABLE companies FORCE ROW LEVEL SECURITY;
        CREATE POLICY company_context ON companies
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

        ALTER TABLE tenant_memberships ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tenant_memberships FORCE ROW LEVEL SECURITY;
        CREATE POLICY membership_tenant_context ON tenant_memberships
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        CREATE POLICY membership_self_read ON tenant_memberships
          FOR SELECT USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

        ALTER TABLE tenant_onboarding_requests ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tenant_onboarding_requests FORCE ROW LEVEL SECURITY;
        CREATE POLICY onboarding_request_write ON tenant_onboarding_requests
          USING (
            user_id = NULLIF(current_setting('app.user_id', true), '')::uuid AND
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
          )
          WITH CHECK (
            user_id = NULLIF(current_setting('app.user_id', true), '')::uuid AND
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
          );
        CREATE POLICY onboarding_request_self_read ON tenant_onboarding_requests
          FOR SELECT USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

        ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
        CREATE POLICY audit_event_tenant_read ON audit_events
          FOR SELECT USING (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
          );
        CREATE POLICY audit_event_scoped_insert ON audit_events
          FOR INSERT WITH CHECK (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid OR
            (
              tenant_id IS NULL AND
              (
                actor_user_id = NULLIF(current_setting('app.user_id', true), '')::uuid OR
                (actor_user_id IS NULL AND event_type IN (
                  'auth.login_failed', 'auth.login_rate_limited'
                ))
              )
            )
          );

        GRANT USAGE ON SCHEMA public TO bf_runtime;
        GRANT SELECT, INSERT, UPDATE ON users TO bf_runtime;
        GRANT SELECT, INSERT, UPDATE ON tenants, companies, tenant_memberships TO bf_runtime;
        GRANT SELECT, INSERT, UPDATE ON auth_sessions, refresh_tokens TO bf_runtime;
        GRANT SELECT, INSERT, UPDATE, DELETE ON login_throttles TO bf_runtime;
        GRANT SELECT, INSERT ON tenant_onboarding_requests TO bf_runtime;
        GRANT SELECT, INSERT ON audit_events TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM bf_runtime;
        REVOKE DELETE ON tenants, companies, tenant_memberships, users FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE audit_events;
        DROP TABLE tenant_onboarding_requests;
        DROP TABLE refresh_tokens;
        DROP TABLE auth_sessions;
        DROP TABLE login_throttles;
        DROP TABLE tenant_memberships;
        ALTER TABLE tenants DROP CONSTRAINT fk_tenants_primary_company;
        DROP TABLE companies;
        DROP TABLE tenants;
        DROP TABLE users;
        DROP FUNCTION businessflow_audit_is_immutable();
        DROP FUNCTION businessflow_require_active_owner();
        """
    )
