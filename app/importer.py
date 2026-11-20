"""Import the existing Excel/CSV deployment tracker."""
import csv
import io

from . import config, db

COLUMN_MAP = {
    "ID": "id",
    "Phase": "phase",
    "REQ#": "req_number",
    "Tech Assign": "tech",
    "Status": "status",
    "Shared Device (Y/N)": "shared_device",
    "Deployment Type": "deployment_type",
    "Device Assigned SN": "new_device_sn",
    "DubSupport Tag (Y/N)": "dubsupport_tag",
    "Accessories": "accessories",
    "Device Location": "location",
    "Assigned To (User)": "user_name",
    "Assigned To NetID": "netid",
    "Department Contact": "dept_contact",
    "Department": "department",
    "Old Device Returned": "old_device_returned",
    "Notes": "notes",
    "Serial Number": "old_device_sn",
    "Last Login": "last_login",
    # optional columns if you add them to the sheet
    "Email": "user_email",
    "Department Contact Email": "dept_contact_email",
}

NO_EMAIL_NETIDS = {"", "N/A", "NA", "UWFTECH"}  # shared/team accounts


def import_csv(text: str, overwrite: bool = False) -> dict:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    created = updated = skipped = 0
    with db.session() as conn:
        for raw in reader:
            row = {}
            for header, value in raw.items():
                if header is None:
                    continue
                field = COLUMN_MAP.get(header.strip())
                if field:
                    row[field] = (value or "").strip()
            if not row.get("id"):
                skipped += 1
                continue
            row["status"] = row.get("status") or "Verify"
            row["netid"] = row.get("netid", "").lower() if row.get("netid", "").upper() \
                not in NO_EMAIL_NETIDS else row.get("netid", "")
            if not row.get("user_email") and row["netid"].upper() not in NO_EMAIL_NETIDS:
                row["user_email"] = f"{row['netid']}@{config.EMAIL_DOMAIN}"
            row["last_login"] = row.get("last_login", "").replace("NETID\\", "")

            existing = db.get_deployment(conn, row["id"])
            if existing and not overwrite:
                skipped += 1
                continue
            row["updated_at"] = db.now_iso()
            if existing:
                db.update_fields(conn, row["id"], row)
                db.log(conn, row["id"], "system", "Re-imported from tracker CSV")
                updated += 1
            else:
                row["created_at"] = row["updated_at"]
                cols = ", ".join(row)
                conn.execute(f"INSERT INTO deployments ({cols}) VALUES ({', '.join('?' * len(row))})",
                             tuple(row.values()))
                db.log(conn, row["id"], "system", "Imported from tracker CSV")
                created += 1
    return {"created": created, "updated": updated, "skipped": skipped}


def export_csv() -> str:
    out = io.StringIO()
    headers = [h for h in COLUMN_MAP] + ["Scheduled Start", "Reminders Sent"]
    w = csv.writer(out)
    w.writerow(headers)
    with db.session() as conn:
        for r in conn.execute("SELECT * FROM deployments ORDER BY id"):
            r = dict(r)
            w.writerow([r[COLUMN_MAP[h]] for h in COLUMN_MAP] + [r["scheduled_start"], r["reminder_count"]])
    return out.getvalue()
