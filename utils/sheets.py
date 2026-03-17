"""
Google Sheets integration.
Writes weekly pricing rows to the shared Google Sheet,
maintaining the exact same format as the existing manual sheet.
"""

import json
import os
from datetime import datetime, date
from typing import Optional
import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Column mapping — must match exactly the sheet header order
# Year, Week, Owner, AM, Partner, Company, DF, SF, MBS, DF Promo,
# Promo in menu, Promocode (CRM), Proper Delivery (Web promo), Comments
COLUMNS = [
    "year", "week", "owner", "am", "partner", "platform",
    "df", "sf", "mbs", "df_promo",
    "promo_menu", "promocode", "web_promo", "comments"
]


def get_current_week_label() -> str:
    """Returns e.g. 'w12' for the current ISO week."""
    week_num = date.today().isocalendar()[1]
    return f"w{week_num}"


def get_current_year() -> int:
    return date.today().year


class SheetsWriter:
    def __init__(self):
        self.sheet_id = os.environ["GOOGLE_SHEET_ID"]
        creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        self.client = gspread.authorize(creds)
        self.spreadsheet = self.client.open_by_key(self.sheet_id)

    def _get_or_create_sheet(self, sheet_name: str = "raw_data") -> gspread.Worksheet:
        """Get the main data worksheet."""
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            # Create with headers if it doesn't exist
            ws = self.spreadsheet.add_worksheet(title=sheet_name, rows=10000, cols=15)
            ws.append_row([
                "Year", "Week", "Owner", "AM", "Partner", "Company",
                "DF", "SF", "MBS", "DF Promo",
                "Promo in menu", "Promocode (CRM)", "Proper Delivery (Web promo)", "Comments"
            ])
            return ws

    def write_weekly_results(
        self,
        results: list[dict],
        competitors_config: dict,
        year: Optional[int] = None,
        week: Optional[str] = None,
    ) -> int:
        """
        Append the week's scraping results to the sheet.
        Returns number of rows written.
        """
        year = year or get_current_year()
        week = week or get_current_week_label()

        # Build lookup for owner/am by partner name
        owner_lookup = {
            c["name"]: {"owner": c["owner"], "am": c["am"]}
            for c in competitors_config["competitors"]
        }

        ws = self._get_or_create_sheet("raw_data")
        rows_to_append = []

        for result in results:
            partner = result.get("partner", "")
            meta = owner_lookup.get(partner, {"owner": "", "am": ""})

            row = [
                year,
                week,
                meta["owner"],
                meta["am"],
                partner,
                result.get("platform", ""),
                result.get("df", ""),
                result.get("sf", ""),
                result.get("mbs", ""),
                result.get("df_promo", "NO"),
                result.get("promo_menu", "NO"),
                result.get("promocode", "NO"),
                result.get("web_promo", ""),
                result.get("comments", ""),
            ]
            rows_to_append.append(row)

        if rows_to_append:
            ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
            print(f"[Sheets] Wrote {len(rows_to_append)} rows for {week} {year}")

        return len(rows_to_append)

    def get_sheet_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}"
