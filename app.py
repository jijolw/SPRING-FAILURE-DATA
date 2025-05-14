import os
import json
import io
import base64
import pandas as pd
import gspread
import matplotlib
matplotlib.use('Agg')  # Use a non-GUI backend
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from datetime import datetime
from collections import Counter
from flask import Flask, render_template, request, redirect, jsonify, Response, send_file
from oauth2client.service_account import ServiceAccountCredentials
import csv
from io import StringIO, BytesIO

app = Flask(__name__)

# Ensure options.json exists
OPTIONS_FILE = "options.json"
if not os.path.exists(OPTIONS_FILE):
    with open(OPTIONS_FILE, "w") as f:
        json.dump({}, f, indent=4)

# Load stored dropdown values from options.json
with open(OPTIONS_FILE, "r") as f:
    stored_options = json.load(f)

# Load Google Sheets credentials
with open("config.json") as config_file:
    config = json.load(config_file)

SPREADSHEET_ID = config["SPREADSHEET_ID"]

# Google Sheets API Setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Check if credentials.json exists (for local use)
if os.path.exists("credentials.json"):
    print("Using local credentials.json file")
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
else:
    # If running on Render, use the environment variable
    credentials_json = os.getenv("GOOGLE_CREDENTIALS")
    if credentials_json:
        print("Using credentials from environment variable")
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(credentials_json), scope)
    else:
        raise ValueError("No credentials found! Provide 'credentials.json' locally or set 'GOOGLE_CREDENTIALS' in Render.")
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID).sheet1  # Open the first sheet

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        form_data = request.form.to_dict(flat=False)
        
        # Check if this is a repeat submission
        repeat_count = int(request.form.get("repeat_count", 1))
        
        columns = sheet.row_values(1)
        
        # Process data for each repeat
        for _ in range(repeat_count):
            data = [", ".join(form_data.get(col, [""])) for col in columns]
            sheet.append_row(data)
        
        return redirect("/")

    # Read existing data from Google Sheets
    data = sheet.get_all_records()
    columns = sheet.row_values(1)

    # Ensure dropdowns load from BOTH Google Sheets and options.json
    df = pd.DataFrame(data)
    dropdown_options = {}

    dropdown_fields = [
        "Coach Code", "Schedule", "Division", "Secondary Suspension Type",
        "Type of Spring", "Colour of Spring", "Type of Failure", "Location", 
        "Reason for Failure", "Remarks", "POH Date", "MFG", "Maintenance Depot"
    ]

    for field in dropdown_fields:
        values_from_sheet = df[field].dropna().astype(str).unique().tolist() if field in df.columns else []
        stored_values = stored_options.get(field, [])  # Get stored values from options.json
        dropdown_options[field] = sorted(set(stored_values + values_from_sheet))  

    return render_template("index.html", columns=columns, dropdown_options=dropdown_options, data=data)

@app.route("/report", methods=["GET"])
def report():
    try:
        # Fetch data from Google Sheets
        data = sheet.get_all_records()
        df = pd.DataFrame(data)

        if df.empty:
            return render_template("error.html", message="No data available to generate the report.")

        # Ensure required columns exist
        required_columns = [
            "Coach Code", "Schedule", "Secondary Suspension Type", "Type of Spring",
            "Type of Failure", "Location", "Reason for Failure", "POH Date", "MFG",
            "Maintenance Depot", "Receipt Date"
        ]
        
        for col in required_columns:
            if col not in df.columns:
                df[col] = ""

        # Normalize data
        for col in df.columns:
            df[col] = df[col].astype(str).str.strip()

        # Apply filters with sanitized keys
        filters = {}
        for col in required_columns:
            if request.args.get(col.replace(" ", "_")):
                filters[col] = request.args.get(col.replace(" ", "_")).strip()

        filtered_df = df.copy()
        for column, value in filters.items():
            if value and column in filtered_df.columns:
                filtered_df = filtered_df[filtered_df[column] == value]

        if filtered_df.empty:
            return render_template(
                "report.html",
                charts={},
                summary_tables={},
                dropdown_options={col: sorted(df[col].dropna().unique().tolist()) for col in required_columns if col in df.columns},
                filtered_table_html="<p>No data matches the selected filters.</p>",
                applied_filters=filters,
                filtered_df=pd.DataFrame(),
                location_counts=pd.Series(),
                failure_counts=pd.Series(),
                coach_codes=pd.Series(),
                reason_counts=pd.Series(),
                current_date=datetime.now().strftime("%B %d, %Y %I:%M %p"),
                insights={}
            )

        filtered_table_html = filtered_df.to_html(index=False, classes="filtered-data-table", border=1)

        # Calculate counts
        location_counts = filtered_df["Location"].value_counts()
        failure_counts = filtered_df["Type of Failure"].value_counts()
        coach_codes = filtered_df["Coach Code"].value_counts()
        reason_counts = filtered_df["Reason for Failure"].value_counts()

        # Prepare chart data as JSON
        charts = {}
        
        # Failure Types Chart
        charts["failure_types"] = {
            "labels": failure_counts.index.tolist() if not failure_counts.empty else [],
            "data": failure_counts.values.tolist() if not failure_counts.empty else [],
            "type": "bar",
            "title": "Failure Type Distribution"
        }

        # Location Distribution Chart
        charts["location_distribution"] = {
            "labels": location_counts.index.tolist() if not location_counts.empty else [],
            "data": location_counts.values.tolist() if not location_counts.empty else [],
            "type": "pie",
            "title": "Failure by Location"
        }

        # Reason Distribution Chart
        charts["reason_distribution"] = {
            "labels": reason_counts.nlargest(7).index.tolist() if not reason_counts.empty else [],
            "data": reason_counts.nlargest(7).values.tolist() if not reason_counts.empty else [],
            "type": "bar",
            "title": "Top Reasons for Failure",
            "horizontal": True
        }

        # Timeline Chart
        if "Receipt Date" in filtered_df.columns and not filtered_df["Receipt Date"].isna().all():
            try:
                filtered_df["Receipt Date"] = pd.to_datetime(filtered_df["Receipt Date"], errors='coerce')
                filtered_df = filtered_df.dropna(subset=["Receipt Date"])
                if not filtered_df.empty:
                    date_counts = filtered_df.groupby(filtered_df["Receipt Date"].dt.date).size()
                    charts["time_trend"] = {
                        "labels": [d.strftime("%Y-%m-%d") for d in date_counts.index] if not date_counts.empty else [],
                        "data": date_counts.values.tolist() if not date_counts.empty else [],
                        "type": "line",
                        "title": "Failure Trend Over Time"
                    }
            except Exception as e:
                print(f"Error creating timeline chart: {str(e)}")

        # Heatmap Chart
        if len(filtered_df) > 5:
            try:
                cross_data = pd.crosstab(filtered_df["Coach Code"], filtered_df["Type of Failure"])
                charts["heatmap"] = {
                    "xLabels": cross_data.columns.tolist() if not cross_data.empty else [],
                    "yLabels": cross_data.index.tolist() if not cross_data.empty else [],
                    "data": cross_data.values.tolist() if not cross_data.empty else [],
                    "type": "matrix",
                    "title": "Coach Code vs Failure Type"
                }
            except Exception as e:
                print(f"Error creating heatmap: {str(e)}")

        # Summary Tables
        summary_tables = {
            "failure_types": failure_counts.reset_index().rename(
                columns={"index": "Type of Failure", "Type of Failure": "Count"}
            ).to_html(index=False, classes="summary-table", border=1) if not failure_counts.empty else "<p>No failure types data available.</p>",
            "locations": location_counts.reset_index().rename(
                columns={"index": "Location", "Location": "Count"}
            ).to_html(index=False, classes="summary-table", border=1) if not location_counts.empty else "<p>No locations data available.</p>",
            "reasons": reason_counts.reset_index().rename(
                columns={"index": "Reason for Failure", "Reason for Failure": "Count"}
            ).to_html(index=False, classes="summary-table", border=1) if not reason_counts.empty else "<p>No reasons data available.</p>"
        }

        dropdown_options = {col: sorted(df[col].dropna().unique().tolist())
                           for col in required_columns if col in df.columns}

        # Insights
        insights = {
            "common_failure": failure_counts.index[0] if not failure_counts.empty else "N/A",
            "common_location": location_counts.index[0] if not location_counts.empty else "N/A",
            "common_reason": reason_counts.index[0] if not reason_counts.empty else "N/A",
            "failure_percentage": round((len(filtered_df) / len(df) * 100), 2) if len(df) > 0 else 0,
            "top_coach_code": coach_codes.index[0] if not coach_codes.empty else "N/A"
        }

        return render_template(
            "report.html",
            charts=charts,
            summary_tables=summary_tables,
            dropdown_options=dropdown_options,
            filtered_table_html=filtered_table_html,
            applied_filters=filters,
            filtered_df=filtered_df,
            location_counts=location_counts,
            failure_counts=failure_counts,
            coach_codes=coach_codes,
            reason_counts=reason_counts,
            current_date=datetime.now().strftime("%B %d, %Y %I:%M %p"),
            insights=insights
        )

    except Exception as e:
        return render_template("error.html", message=f"Error occurred: {str(e)}")
@app.route("/delete_entry", methods=["POST"])
def delete_entry():
    unique_id = request.json.get("unique_id")
    identifier_column = request.json.get("identifier_column", "Coach No")

    if not unique_id:
        return jsonify({"success": False, "error": "Invalid identifier"})

    # Fetch all data again to ensure latest updates
    data = sheet.get_all_records()
    df = pd.DataFrame(data)

    if identifier_column not in df.columns:
        return jsonify({"success": False, "error": "Invalid column"})

    # Convert all values to strings to ensure matching
    df[identifier_column] = df[identifier_column].astype(str).str.strip()
    unique_id = str(unique_id).strip()

    # Find the row index (Pandas index, not Google Sheets index)
    row_index = df[df[identifier_column] == unique_id].index.tolist()

    if not row_index:
        return jsonify({"success": False, "error": f"Entry with {identifier_column}: {unique_id} not found"})

    google_sheets_row = row_index[0] + 2  # Adjusting for 1-based index + header

    try:
        sheet.delete_rows(google_sheets_row)
        return jsonify({"success": True, "message": f"Entry with {identifier_column}: {unique_id} deleted successfully!"})
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to delete entry: {str(e)}"})

@app.route("/add_column", methods=["POST"])
def add_column():
    new_column = request.json.get("column_name")
    
    if not new_column:
        return jsonify({"success": False, "error": "Invalid column name"})
    
    columns = sheet.row_values(1)
    if new_column not in columns:
        columns.append(new_column)
        sheet.insert_row(columns, 1)
    
    return jsonify({"success": True, "message": f"Column '{new_column}' added successfully!"})

@app.route("/add_value", methods=["POST"])
def add_value():
    new_value = request.json.get("value")
    dropdown_type = request.json.get("dropdown")

    if not new_value or not dropdown_type:
        return jsonify({"success": False, "error": "Invalid input"})

    if dropdown_type not in stored_options:
        stored_options[dropdown_type] = []

    if new_value not in stored_options[dropdown_type]:
        stored_options[dropdown_type].append(new_value)
        with open(OPTIONS_FILE, "w") as f:
            json.dump(stored_options, f, indent=4)

    return jsonify({"success": True, "message": f"Value '{new_value}' added successfully!"})

@app.route("/delete_value", methods=["POST"])
def delete_value():
    value_to_delete = request.json.get("value")
    dropdown_type = request.json.get("dropdown")

    if not value_to_delete or not dropdown_type:
        return jsonify({"success": False, "error": "Invalid input"})

    if dropdown_type in stored_options and value_to_delete in stored_options[dropdown_type]:
        stored_options[dropdown_type].remove(value_to_delete)
        with open(OPTIONS_FILE, "w") as f:
            json.dump(stored_options, f, indent=4)

    return jsonify({"success": True, "message": f"Value '{value_to_delete}' removed successfully!"})

@app.route("/duplicate_entry", methods=["POST"])
def duplicate_entry():
    entry_id = request.json.get("entry_id")
    identifier_column = request.json.get("identifier_column", "Coach No")
    
    if not entry_id:
        return jsonify({"success": False, "error": "Invalid entry ID"})
    
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    
    if identifier_column not in df.columns:
        return jsonify({"success": False, "error": "Invalid column"})
    
    # Find the entry to duplicate
    df[identifier_column] = df[identifier_column].astype(str).str.strip()
    entry_id = str(entry_id).strip()
    
    entry = df[df[identifier_column] == entry_id]
    
    if entry.empty:
        return jsonify({"success": False, "error": f"Entry with {identifier_column}: {entry_id} not found"})
    
    # Get the row to duplicate
    columns = sheet.row_values(1)
    row_to_duplicate = [entry.iloc[0].get(col, "") for col in columns]
    
    # Add the duplicated row
    sheet.append_row(row_to_duplicate)
    
    return jsonify({"success": True, "message": f"Entry duplicated successfully!"})

@app.route("/export_report", methods=["GET"])
def export_report():
    try:
        # Fetch data from Google Sheets
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        if df.empty:
            return render_template("error.html", message="No data available to export.")

        # Ensure required columns exist
        required_columns = [
            "Coach Code", "Schedule", "Secondary Suspension Type", "Type of Spring",
            "Type of Failure", "Location", "Reason for Failure", "POH Date", "MFG",
            "Maintenance Depot", "Receipt Date"
        ]
        for col in required_columns:
            if col not in df.columns:
                df[col] = ""
        for col in df.columns:
            df[col] = df[col].astype(str).str.strip()

        # Apply filters
        filters = {col: request.args.get(col.replace(" ", "_")).strip() for col in required_columns if request.args.get(col.replace(" ", "_"))}
        filtered_df = df.copy()
        for column, value in filters.items():
            if value and column in filtered_df.columns:
                filtered_df = filtered_df[filtered_df[column] == value]

        if filtered_df.empty:
            return render_template("error.html", message="No data matches the selected filters for export.")

        export_format = request.args.get("format", "csv")
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        if export_format == "csv":
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(filtered_df.columns)
            for _, row in filtered_df.iterrows():
                writer.writerow(row.values)
            output.seek(0)
            return Response(
                output.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment;filename=report_{timestamp}.csv"}
            )
        elif export_format == "xlsx":
            output = BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                filtered_df.to_excel(writer, index=False, sheet_name="Report")
            output.seek(0)
            return send_file(
                output,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                download_name=f"report_{timestamp}.xlsx",
                as_attachment=True
            )
        else:
            return render_template("error.html", message="Unsupported export format.")

    except Exception as e:
        return render_template("error.html", message=f"Error occurred during export: {str(e)}")

if __name__ == "__main__":
    app.run(debug=True)