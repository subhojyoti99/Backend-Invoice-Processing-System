import os
import base64
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from anthropic import Anthropic
import aspose.pdf as ap
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import firebase_admin
from firebase_admin import credentials, firestore
import json
from datetime import datetime

# Load API keys from environment variables
load_dotenv()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Initialize Firebase
cred = credentials.Certificate(
    "invoice-processing-system-firebase-adminsdk-fbsvc-5f0705eeec.json"
)
firebase_admin.initialize_app(cred)

# firebase_admin.initialize_app(cred, {
#     'storageBucket': 'invoice-processing-system.appspot.com'  # Replace with your Firebase project ID
# })
# bucket = storage.bucket()
db = firestore.client()
print("-------------", db)

# Initialize FastAPI app
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

CSV_FILE = "invoice_data.csv"
UPLOADS_FOLDER = "uploads"


class InvoiceData(BaseModel):
    Is_Invoice: bool = Field(
        ..., description="Indicates whether the it is a invoice or not"
    )
    Invoice_Number: str = Field(
        ..., description="Invoice number extracted from the document"
    )
    Invoice_Date: str = Field(..., description="Date of the invoice")
    Net_SUM: str = Field(..., description="Net total amount before VAT")
    Gross_SUM: str = Field(..., description="Gross total including VAT")
    VAT_Percentage: str = Field(..., description="VAT percentage")
    VAT_Amount: str = Field(..., description="VAT amount in invoice currency (EUR)")
    Invoice_Sender_Name: str = Field(..., description="Sender Name")
    Invoice_Sender_Address: str = Field(..., description="Sender Address")
    Invoice_Recipient_Name: str = Field(..., description="Recipient Name")
    Invoice_Recipient_Address: str = Field(..., description="Recipient Address")
    Invoice_Payment_Terms: str = Field(None, description="Payment terms (e.g., NET 30)")
    Payment_Method: str = Field(None, description="Payment method used for the invoice")
    Category_Classification: str = Field(
        None, description="Bookkeeping category (e.g., SOFTWARE, Electronics)"
    )
    Is_Subscription: bool = Field(
        ..., description="Indicates whether the invoice is for a subscription"
    )
    START_Date: str = Field(
        None,
        description="Subscription start date, applicable only if is_Subscription is True",
    )
    END_Date: str = Field(
        None,
        description="Subscription end date, applicable only if is_Subscription is True",
    )
    Tips: str = Field(None, description="If any tips mentioned in the invoice")
    Original_Filename: str = Field(None, description="Original filename of the invoice")
    Upload_Timestamp: str = Field(
        None, description="Timestamp when invoice was uploaded"
    )
    # PDF_Storage_Path: str = Field(None, description="Path to stored PDF in Firebase Storage")


# def upload_pdf_to_firebase(file_path: str, invoice_number: str):
#     """Upload PDF to Firebase Storage"""
#     try:
#         # Create a unique path for the PDF in Firebase Storage
#         storage_path = f"invoices/{invoice_number}/{os.path.basename(file_path)}"

#         # Upload file to Firebase Storage
#         blob = bucket.blob(storage_path)
#         blob.upload_from_filename(file_path)

#         # Make the file publicly accessible (optional - you may want more restricted access)
#         blob.make_public()

#         return storage_path, blob.public_url
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Error uploading PDF to Firebase: {str(e)}")


def pdf_to_image(pdf_path: str) -> str:
    """Convert PDF to image using Aspose.PDF"""
    try:
        document = ap.Document(pdf_path)
        resolution = ap.devices.Resolution(300)
        jpg_device = ap.devices.JpegDevice(resolution)
        image_path = os.path.splitext(pdf_path)[0] + ".jpg"
        jpg_device.process(document.pages[1], image_path)
        return image_path
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error converting PDF to image: {str(e)}"
        )


def encode_image_to_base64(image_path: str) -> str:
    """Encode image to Base64"""
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error encoding image: {str(e)}")


def analyze_invoice(image_path: str, filename: str):
    """Send image to Claude for invoice extraction"""
    try:
        if not ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key not set.")

        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        base64_image = encode_image_to_base64(image_path)

        system_prompt = """You will be analyse and extract all the information from this invoice. Make sure to response in English.
        Extract the following fields from the invoice:
        {{
            "Is_Invoice": "Indicates whether the it is a invoice or not",
            "Invoice_Number" : "Invoice number extracted from the document",
            "Invoice_Date": "Date of the invoice",
            "Net_SUM": "Net total amount before VAT",
            "Gross_SUM": "including VAT",
            "VAT_Percentage":"VAT percentage",
            "VAT_Amount": "VAT amount in invoice currency (EUR),
            "Invoice_Sender_Name": "Name",
            "Invoice_Sender_Address": "Address",
            "Invoice_Recipient_Name": "Name",
            "Invoice_Recipient_Address": "Address",
            "Invoice_Payment_Terms": "Payment terms (e.g., NET 30)",
            "Category_Classification": (Kostenstelle)( i.e. categorize it like it would be in an Austrian bookkeeping into: SOFTWARE, Electronic, Food & Beverage etc.),
            "Payment_Method": "Payment method used for the invoice",
            "Is_Subscription": "Indicates whether the invoice is for a subscription",
            "START_Date": (if subscription else N/A),
            "END_Date": (if subscription else N/A),
            "Tips": "If any value mentioned in the invoice(provide only the value)."
        }}
            
        If any field is missing, mark as 'N/A'.
        Most Important Note: Provide currency sign before any amount value.  
        Most Important Note: If there is any hand-written portion identified, analyse it carefully and respond.
        Provide the extracted data only. Do not required to response ```Json. 
        """

        response = client.messages.create(
            model="claude-3-7-sonnet-20250219",
            max_tokens=1000,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Extract invoice details and return structured JSON.",
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64_image,
                            },
                        },
                    ],
                }
            ],
        )

        extracted_data = response.content[0].text
        structured_output = InvoiceData.model_validate_json(extracted_data)
        print("check_!", structured_output)
        # Add filename and timestamp
        structured_output.Original_Filename = filename
        structured_output.Upload_Timestamp = datetime.now().isoformat()
        print("check_@", structured_output)
        return structured_output
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error analyzing invoice: {str(e)}"
        )


# @app.post("/upload-invoice")
# async def upload_invoice(file: UploadFile = File(...)):
#     """Endpoint to upload an invoice PDF and extract details"""
#     try:
#         print("hello 1")
#         file_location = f"uploads/{file.filename}"
#         os.makedirs("uploads", exist_ok=True)
#         print("hello 2")

#         # Save uploaded file
#         with open(file_location, "wb") as f:
#             f.write(await file.read())
#         print("hello 3")

#         # Convert PDF to image
#         image_path = pdf_to_image(file_location)
#         print("hello 4")

#         # Extract invoice data
#         extracted_data = analyze_invoice(image_path)
#         print("hello 5")
#         extracted_data = extracted_data.model_dump()
#         print(extracted_data)

#         df = pd.DataFrame([extracted_data])

#         if os.path.exists(CSV_FILE):
#             df.to_csv(CSV_FILE, mode="a", header=False, index=False)
#         else:
#             df.to_csv(CSV_FILE, index=False)

#         return {"status": "success", "extracted_data": extracted_data}

#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))


@app.post("/upload-invoice")
async def upload_invoice(file: UploadFile = File(...)):
    """Endpoint to upload an invoice PDF and extract details"""
    try:
        os.makedirs(UPLOADS_FOLDER, exist_ok=True)
        print("check 1")
        file_location = f"{UPLOADS_FOLDER}/{file.filename}"
        print("check 2")
        with open(file_location, "wb") as f:
            f.write(await file.read())
        print("check 3")
        image_path = pdf_to_image(file_location)
        print("check 4", image_path)
        extracted_data = analyze_invoice(image_path, file.filename)
        print("check 5")
        invoice_dict = extracted_data.model_dump()

        # Upload PDF to Firebase Storage
        # storage_path, public_url = upload_pdf_to_firebase(file_location, invoice_dict['Invoice_Number'])

        # invoice_dict['PDF_Storage_Path'] = storage_path
        # invoice_dict['PDF_Public_URL'] = public_url

        invoice_ref = db.collection("invoices").document(invoice_dict["Invoice_Number"])
        print("check 6")
        invoice_ref.set(invoice_dict)
        print("check 7")
        return {"status": "success", "extracted_data": invoice_dict}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# @app.get("/get-invoices")
# async def get_invoices():
#     try:
#         # files = os.listdir(UPLOADS_FOLDER)
#         files = [f for f in os.listdir(UPLOADS_FOLDER) if f.lower().endswith(".pdf")]
#         return JSONResponse(content=files)
#     except Exception as e:
#         return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/get-invoices")
async def get_invoices():
    try:
        # Get all invoices from Firestore
        invoices_ref = db.collection("invoices")
        docs = invoices_ref.stream()

        # invoices = []
        # for doc in docs:
        #     invoice_data = doc.to_dict()
        #     invoices.append(invoice_data)
        filenames = [
            doc.to_dict().get("Original_Filename")
            for doc in docs
            if "Original_Filename" in doc.to_dict()
        ]

        return JSONResponse(content=filenames)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# @app.get("/view-csv")
# def view_csv():
#     if os.path.exists(CSV_FILE):
#         with open(CSV_FILE, "r", encoding="utf-8") as f:
#             csv_content = f.read()
#         return Response(content=csv_content, media_type="text/plain")
#     return JSONResponse(content={"error": "CSV file not found"}, status_code=404)


@app.get("/view-csv")
async def view_csv():
    try:
        # Get all invoices from Firestore
        invoices_ref = db.collection("invoices")
        docs = invoices_ref.stream()

        invoices = []
        for doc in docs:
            invoice_data = doc.to_dict()
            invoices.append(invoice_data)

        return JSONResponse(content=invoices)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# @app.get("/download-csv")
# def download_csv():
#     if os.path.exists(CSV_FILE):
#         return FileResponse(
#             CSV_FILE, filename="invoice_data.csv", media_type="text/csv"
#         )
#     return {"error": "CSV file not found"}


# @app.get("/get-invoices")
# async def get_invoices():
#     try:
#         # Get all invoices from Firestore
#         invoices_ref = db.collection("invoices")
#         docs = invoices_ref.stream()

#         invoices = []
#         for doc in docs:
#             invoice_data = doc.to_dict()
#             invoices.append(invoice_data)

#         return JSONResponse(content=invoices)
#     except Exception as e:
#         return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/get-invoice/{invoice_number}")
async def get_invoice(invoice_number: str):
    try:
        invoice_ref = db.collection("invoices").document(invoice_number)
        invoice = invoice_ref.get()

        if invoice.exists:
            return JSONResponse(content=invoice.to_dict())
        else:
            return JSONResponse(content={"error": "Invoice not found"}, status_code=404)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/download-invoices-csv")
def download_invoices_csv():
    try:
        # Get all invoices from Firestore
        invoices_ref = db.collection("invoices")
        docs = invoices_ref.stream()

        invoices = []
        for doc in docs:
            invoices.append(doc.to_dict())

        if not invoices:
            return JSONResponse(content={"error": "No invoices found"}, status_code=404)

        # Convert to DataFrame and save as CSV
        df = pd.DataFrame(invoices)
        csv_path = "invoice_data_export.csv"
        df.to_csv(csv_path, index=False)

        return FileResponse(
            csv_path, filename="invoice_data.csv", media_type="text/csv"
        )
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.delete("/delete-invoice/{invoice_number}")
async def delete_invoice(invoice_number: str):
    try:
        invoice_ref = db.collection("invoices").document(invoice_number)
        invoice = invoice_ref.get()

        if invoice.exists:
            invoice_ref.delete()
            return {
                "status": "success",
                "message": f"Invoice {invoice_number} deleted successfully",
            }
        else:
            return JSONResponse(content={"error": "Invoice not found"}, status_code=404)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
