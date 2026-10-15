from django.db import migrations


FORWARD_SQL = """
CREATE FUNCTION inventory_reject_posted_record_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Posted inventory records are immutable; create a reversal instead.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER inventory_stockmovement_immutable
BEFORE UPDATE OR DELETE ON inventory_stockmovement
FOR EACH ROW EXECUTE FUNCTION inventory_reject_posted_record_change();

CREATE TRIGGER inventory_stockledgerentry_immutable
BEFORE UPDATE OR DELETE ON inventory_stockledgerentry
FOR EACH ROW EXECUTE FUNCTION inventory_reject_posted_record_change();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS inventory_stockledgerentry_immutable ON inventory_stockledgerentry;
DROP TRIGGER IF EXISTS inventory_stockmovement_immutable ON inventory_stockmovement;
DROP FUNCTION IF EXISTS inventory_reject_posted_record_change();
"""


def install_postgresql_triggers(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FORWARD_SQL)


def remove_postgresql_triggers(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0003_stockmovement_unique_stock_source_reference")]

    operations = [
        migrations.RunPython(install_postgresql_triggers, remove_postgresql_triggers),
    ]
