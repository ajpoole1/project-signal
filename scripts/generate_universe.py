#!/usr/bin/env python3
"""
generate_universe.py — Expand ticker_universe.json to ~2,500 tickers.

Usage:
    python scripts/generate_universe.py              # dry-run, prints summary
    python scripts/generate_universe.py --write      # writes to config/ticker_universe.json
    python scripts/generate_universe.py --write --backup  # backs up existing file first
"""

import argparse
import json
import shutil
from datetime import date
from pathlib import Path

# fmt: off
# Structured by EODHD sector, then US/CAD, then large/mid/small.
# Format: "TICKER": ("exchange", "cap_tier", "eodhd_sector", "eodhd_industry")
NEW_TICKERS: dict[str, tuple[str, str, str, str]] = {

    # -- ENERGY -------------------------------------------------------------------
    # US — Large
    "XOM":    ("us",  "large", "Energy", "Oil & Gas Integrated"),
    "CVX":    ("us",  "large", "Energy", "Oil & Gas Integrated"),
    "COP":    ("us",  "large", "Energy", "Oil & Gas E&P"),
    "EOG":    ("us",  "large", "Energy", "Oil & Gas E&P"),
    "SLB":    ("us",  "large", "Energy", "Oil & Gas Equipment & Services"),
    "PSX":    ("us",  "large", "Energy", "Oil & Gas Refining & Marketing"),
    # US — Mid
    "DVN":    ("us",  "mid",   "Energy", "Oil & Gas E&P"),
    "MRO":    ("us",  "mid",   "Energy", "Oil & Gas E&P"),
    "OVV":    ("us",  "mid",   "Energy", "Oil & Gas E&P"),
    "MTDR":   ("us",  "mid",   "Energy", "Oil & Gas E&P"),
    "CHRD":   ("us",  "mid",   "Energy", "Oil & Gas E&P"),
    "HAL":    ("us",  "mid",   "Energy", "Oil & Gas Equipment & Services"),
    "BKR":    ("us",  "mid",   "Energy", "Oil & Gas Equipment & Services"),
    # US — Small
    "CIVI":   ("us",  "small", "Energy", "Oil & Gas E&P"),
    "NOG":    ("us",  "small", "Energy", "Oil & Gas E&P"),
    "TALO":   ("us",  "small", "Energy", "Oil & Gas E&P"),
    "REX":    ("us",  "small", "Energy", "Oil & Gas Refining & Marketing"),
    "PTEN":   ("us",  "small", "Energy", "Oil & Gas Equipment & Services"),
    # CAD — Large
    "CNQ.TO":  ("tsx", "large", "Energy", "Oil & Gas E&P"),
    "SU.TO":   ("tsx", "large", "Energy", "Oil & Gas Integrated"),
    "IMO.TO":  ("tsx", "large", "Energy", "Oil & Gas Integrated"),
    "CVE.TO":  ("tsx", "large", "Energy", "Oil & Gas E&P"),
    "TRP.TO":  ("tsx", "large", "Energy", "Oil & Gas Storage & Transportation"),
    "ENB.TO":  ("tsx", "large", "Energy", "Oil & Gas Storage & Transportation"),
    # CAD — Mid
    "MEG.TO":  ("tsx", "mid",   "Energy", "Oil & Gas E&P"),
    "VET.TO":  ("tsx", "mid",   "Energy", "Oil & Gas E&P"),
    "PEY.TO":  ("tsx", "mid",   "Energy", "Oil & Gas E&P"),
    "TVE.TO":  ("tsx", "mid",   "Energy", "Oil & Gas E&P"),
    "WCP.TO":  ("tsx", "mid",   "Energy", "Oil & Gas E&P"),
    "PKI.TO":  ("tsx", "mid",   "Energy", "Oil & Gas Equipment & Services"),
    "SES.TO":  ("tsx", "mid",   "Energy", "Oil & Gas Equipment & Services"),
    # CAD — Small
    "BTE.TO":  ("tsx", "small", "Energy", "Oil & Gas E&P"),
    "GXE.TO":  ("tsx", "small", "Energy", "Oil & Gas E&P"),
    "POU.TO":  ("tsx", "small", "Energy", "Oil & Gas E&P"),
    "KEL.TO":  ("tsx", "small", "Energy", "Oil & Gas E&P"),
    "DML.TO":  ("tsx", "small", "Energy", "Uranium"),
    "NXE.TO":  ("tsx", "small", "Energy", "Uranium"),

    # -- BASIC MATERIALS ----------------------------------------------------------
    # US — Large
    "LIN":    ("us",  "large", "Basic Materials", "Specialty Chemicals"),
    "APD":    ("us",  "large", "Basic Materials", "Specialty Chemicals"),
    "ECL":    ("us",  "large", "Basic Materials", "Specialty Chemicals"),
    "FCX":    ("us",  "large", "Basic Materials", "Copper"),
    "NUE":    ("us",  "large", "Basic Materials", "Steel"),
    # US — Mid
    "MP":     ("us",  "mid",   "Basic Materials", "Other Industrial Metals & Mining"),
    "SLVM":   ("us",  "mid",   "Basic Materials", "Specialty Chemicals"),
    "TROX":   ("us",  "mid",   "Basic Materials", "Specialty Chemicals"),
    "HWKN":   ("us",  "mid",   "Basic Materials", "Specialty Chemicals"),
    "CMC":    ("us",  "mid",   "Basic Materials", "Steel"),
    "STLD":   ("us",  "mid",   "Basic Materials", "Steel"),
    "RS":     ("us",  "mid",   "Basic Materials", "Steel"),
    # US — Small
    "KALU":   ("us",  "small", "Basic Materials", "Aluminum"),
    "MTRN":   ("us",  "small", "Basic Materials", "Other Industrial Metals & Mining"),
    "ASIX":   ("us",  "small", "Basic Materials", "Specialty Chemicals"),
    "RYAM":   ("us",  "small", "Basic Materials", "Paper & Paper Products"),
    "SXT":    ("us",  "small", "Basic Materials", "Specialty Chemicals"),
    # CAD — Large
    "WPM.TO":    ("tsx", "large", "Basic Materials", "Gold"),
    "AEM.TO":    ("tsx", "large", "Basic Materials", "Gold"),
    "TECK-B.TO": ("tsx", "large", "Basic Materials", "Other Industrial Metals & Mining"),
    "FM.TO":     ("tsx", "large", "Basic Materials", "Copper"),
    "HBM.TO":    ("tsx", "large", "Basic Materials", "Copper"),
    # CAD — Mid
    "EQX.TO":    ("tsx", "mid",   "Basic Materials", "Gold"),
    "SKE.TO":    ("tsx", "mid",   "Basic Materials", "Gold"),
    "CG.TO":     ("tsx", "mid",   "Basic Materials", "Gold"),
    "SSL.TO":    ("tsx", "mid",   "Basic Materials", "Gold"),
    "ARIS.TO":   ("tsx", "mid",   "Basic Materials", "Gold"),
    "FVI.TO":    ("tsx", "mid",   "Basic Materials", "Silver"),
    "MAG.TO":    ("tsx", "mid",   "Basic Materials", "Silver"),
    # CAD — Small
    "SEA.TO":    ("tsx", "small", "Basic Materials", "Gold"),
    "OR.TO":     ("tsx", "small", "Basic Materials", "Gold"),
    "MUX.TO":    ("tsx", "small", "Basic Materials", "Gold"),
    "EOX.V":     ("tsx_venture", "small", "Basic Materials", "Gold"),
    "SWA.TO":    ("tsx", "small", "Basic Materials", "Copper"),

    # -- INDUSTRIALS --------------------------------------------------------------
    # US — Large
    "HON":    ("us",  "large", "Industrials", "Diversified Industrials"),
    "UPS":    ("us",  "large", "Industrials", "Integrated Freight & Logistics"),
    "GE":     ("us",  "large", "Industrials", "Aerospace & Defense"),
    "CAT":    ("us",  "large", "Industrials", "Construction Machinery & Heavy Trucks"),
    "BA":     ("us",  "large", "Industrials", "Aerospace & Defense"),
    "FDX":    ("us",  "large", "Industrials", "Integrated Freight & Logistics"),
    # US — Mid
    "GNRC":   ("us",  "mid",   "Industrials", "Electrical Components & Equipment"),
    "AXON":   ("us",  "mid",   "Industrials", "Security & Alarm Services"),
    "GTLS":   ("us",  "mid",   "Industrials", "Industrial Machinery"),
    "TREX":   ("us",  "mid",   "Industrials", "Building Products & Equipment"),
    "HRI":    ("us",  "mid",   "Industrials", "Rental & Leasing Services"),
    "MIDD":   ("us",  "mid",   "Industrials", "Industrial Machinery"),
    # US — Small
    "KFRC":   ("us",  "small", "Industrials", "Staffing & Employment Services"),
    "DLX":    ("us",  "small", "Industrials", "Business Equipment & Supplies"),
    "ARCB":   ("us",  "small", "Industrials", "Trucking"),
    "GBX":    ("us",  "small", "Industrials", "Railroads"),
    "HURN":   ("us",  "small", "Industrials", "Consulting Services"),
    # CAD — Large
    "CNR.TO":  ("tsx", "large", "Industrials", "Railroads"),
    "CP.TO":   ("tsx", "large", "Industrials", "Railroads"),
    "WSP.TO":  ("tsx", "large", "Industrials", "Engineering & Construction"),
    "TFI.TO":  ("tsx", "large", "Industrials", "Trucking"),
    "SNC.TO":  ("tsx", "large", "Industrials", "Engineering & Construction"),
    # CAD — Mid
    "EIF.TO":  ("tsx", "mid",   "Industrials", "Airlines"),
    "STN.TO":  ("tsx", "mid",   "Industrials", "Engineering & Construction"),
    "TIH.TO":  ("tsx", "mid",   "Industrials", "Diversified Industrials"),
    "BDT.TO":  ("tsx", "mid",   "Industrials", "Specialty Business Services"),
    "ATA.TO":  ("tsx", "mid",   "Industrials", "Aerospace & Defense"),
    "NFI.TO":  ("tsx", "mid",   "Industrials", "Construction Machinery & Heavy Trucks"),
    # CAD — Small
    "HRX.TO":  ("tsx", "small", "Industrials", "Aerospace & Defense"),
    "MAL.TO":  ("tsx", "small", "Industrials", "Aerospace & Defense"),
    "FTG.TO":  ("tsx", "small", "Industrials", "Aerospace & Defense"),
    "BDGI.TO": ("tsx", "small", "Industrials", "Engineering & Construction"),
    "ZCL.TO":  ("tsx", "small", "Industrials", "Industrial Machinery"),

    # -- CONSUMER CYCLICAL --------------------------------------------------------
    # US — Large
    "MCD":    ("us",  "large", "Consumer Cyclical", "Restaurants"),
    "SBUX":   ("us",  "large", "Consumer Cyclical", "Restaurants"),
    "TJX":    ("us",  "large", "Consumer Cyclical", "Apparel Retail"),
    "LOW":    ("us",  "large", "Consumer Cyclical", "Home Improvement Retail"),
    "F":      ("us",  "large", "Consumer Cyclical", "Auto Manufacturers"),
    "GM":     ("us",  "large", "Consumer Cyclical", "Auto Manufacturers"),
    # US — Mid
    "RH":     ("us",  "mid",   "Consumer Cyclical", "Home Furnishings & Fixtures"),
    "BOOT":   ("us",  "mid",   "Consumer Cyclical", "Apparel Retail"),
    "DKNG":   ("us",  "mid",   "Consumer Cyclical", "Gambling"),
    "MTN":    ("us",  "mid",   "Consumer Cyclical", "Leisure"),
    "BROS":   ("us",  "mid",   "Consumer Cyclical", "Restaurants"),
    "SHAK":   ("us",  "mid",   "Consumer Cyclical", "Restaurants"),
    "SIX":    ("us",  "mid",   "Consumer Cyclical", "Leisure"),
    # US — Small
    "PRPL":   ("us",  "small", "Consumer Cyclical", "Home Furnishings & Fixtures"),
    "XPOF":   ("us",  "small", "Consumer Cyclical", "Leisure"),
    "LOVE":   ("us",  "small", "Consumer Cyclical", "Home Furnishings & Fixtures"),
    "SKIN":   ("us",  "small", "Consumer Cyclical", "Personal Products"),
    "DENN":   ("us",  "small", "Consumer Cyclical", "Restaurants"),
    "JACK":   ("us",  "small", "Consumer Cyclical", "Restaurants"),
    # CAD — Large
    "MG.TO":     ("tsx", "large", "Consumer Cyclical", "Auto Parts"),
    "CTC-A.TO":  ("tsx", "large", "Consumer Cyclical", "Specialty Retail"),
    "ATD.TO":    ("tsx", "large", "Consumer Cyclical", "Specialty Retail"),
    "LNR.TO":    ("tsx", "large", "Consumer Cyclical", "Auto Parts"),
    # CAD — Mid
    "TOY.TO":    ("tsx", "mid",   "Consumer Cyclical", "Leisure"),
    "GOOS.TO":   ("tsx", "mid",   "Consumer Cyclical", "Apparel Manufacturing"),
    "ZZZ.TO":    ("tsx", "mid",   "Consumer Cyclical", "Specialty Retail"),
    "DOO.TO":    ("tsx", "mid",   "Consumer Cyclical", "Recreational Vehicles"),
    "MRE.TO":    ("tsx", "mid",   "Consumer Cyclical", "Auto Parts"),
    # CAD — Small
    "PBH.TO":    ("tsx", "small", "Consumer Cyclical", "Packaged Foods"),
    "JWEL.TO":   ("tsx", "small", "Consumer Cyclical", "Luxury Goods"),
    "OGO.TO":    ("tsx", "small", "Consumer Cyclical", "Specialty Retail"),
    "GIL.TO":    ("tsx", "small", "Consumer Cyclical", "Apparel Manufacturing"),

    # -- CONSUMER DEFENSIVE -------------------------------------------------------
    # US — Large
    "WMT":    ("us",  "large", "Consumer Defensive", "Discount Stores"),
    "PG":     ("us",  "large", "Consumer Defensive", "Household & Personal Products"),
    "KO":     ("us",  "large", "Consumer Defensive", "Beverages-Non-Alcoholic"),
    "PEP":    ("us",  "large", "Consumer Defensive", "Beverages-Non-Alcoholic"),
    "COST":   ("us",  "large", "Consumer Defensive", "Discount Stores"),
    "PM":     ("us",  "large", "Consumer Defensive", "Tobacco"),
    # US — Mid
    "POST":   ("us",  "mid",   "Consumer Defensive", "Packaged Foods"),
    "SFM":    ("us",  "mid",   "Consumer Defensive", "Grocery Stores"),
    "INGR":   ("us",  "mid",   "Consumer Defensive", "Packaged Foods"),
    "HRL":    ("us",  "mid",   "Consumer Defensive", "Packaged Foods"),
    "MKC":    ("us",  "mid",   "Consumer Defensive", "Packaged Foods"),
    "KMB":    ("us",  "mid",   "Consumer Defensive", "Household & Personal Products"),
    "CLX":    ("us",  "mid",   "Consumer Defensive", "Household & Personal Products"),
    # US — Small
    "JJSF":   ("us",  "small", "Consumer Defensive", "Packaged Foods"),
    "RMCF":   ("us",  "small", "Consumer Defensive", "Confectioners"),
    "DNUT":   ("us",  "small", "Consumer Defensive", "Packaged Foods"),
    "WVVI":   ("us",  "small", "Consumer Defensive", "Beverages-Wineries & Distilleries"),
    "FIZZ":   ("us",  "small", "Consumer Defensive", "Beverages-Non-Alcoholic"),
    "CELH":   ("us",  "small", "Consumer Defensive", "Beverages-Non-Alcoholic"),
    # CAD — Large
    "L.TO":      ("tsx", "large", "Consumer Defensive", "Grocery Stores"),
    "MRU.TO":    ("tsx", "large", "Consumer Defensive", "Grocery Stores"),
    "SAP.TO":    ("tsx", "large", "Consumer Defensive", "Beverages-Brewers"),
    "EMP-A.TO":  ("tsx", "large", "Consumer Defensive", "Grocery Stores"),
    # CAD — Mid
    "NWC.TO":    ("tsx", "mid",   "Consumer Defensive", "Grocery Stores"),
    "PZA.TO":    ("tsx", "mid",   "Consumer Defensive", "Restaurants"),
    "BPF-UN.TO": ("tsx", "mid",   "Consumer Defensive", "Restaurants"),
    "RSI.TO":    ("tsx", "mid",   "Consumer Defensive", "Grocery Stores"),
    "PRMW.TO":   ("tsx", "mid",   "Consumer Defensive", "Beverages-Non-Alcoholic"),
    # CAD — Small
    "SRV-UN.TO": ("tsx", "small", "Consumer Defensive", "Restaurants"),
    "CARA.TO":   ("tsx", "small", "Consumer Defensive", "Restaurants"),
    "TSGI.TO":   ("tsx", "small", "Consumer Defensive", "Gambling"),

    # -- HEALTHCARE ---------------------------------------------------------------
    # US — Large
    "JNJ":    ("us",  "large", "Healthcare", "Drug Manufacturers-General"),
    "UNH":    ("us",  "large", "Healthcare", "Healthcare Plans"),
    "ABT":    ("us",  "large", "Healthcare", "Medical Devices"),
    "LLY":    ("us",  "large", "Healthcare", "Drug Manufacturers-General"),
    "PFE":    ("us",  "large", "Healthcare", "Drug Manufacturers-General"),
    "MRK":    ("us",  "large", "Healthcare", "Drug Manufacturers-General"),
    "BMY":    ("us",  "large", "Healthcare", "Drug Manufacturers-General"),
    # US — Mid
    "PODD":   ("us",  "mid",   "Healthcare", "Medical Devices"),
    "HIMS":   ("us",  "mid",   "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "INSP":   ("us",  "mid",   "Healthcare", "Medical Devices"),
    "ITGR":   ("us",  "mid",   "Healthcare", "Medical Instruments & Supplies"),
    "OMCL":   ("us",  "mid",   "Healthcare", "Health Information Services"),
    "ACAD":   ("us",  "mid",   "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "TNDM":   ("us",  "mid",   "Healthcare", "Medical Devices"),
    # US — Small
    "NVCR":   ("us",  "small", "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "CRVS":   ("us",  "small", "Healthcare", "Biotechnology"),
    "PTGX":   ("us",  "small", "Healthcare", "Biotechnology"),
    "PRGO":   ("us",  "small", "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "SENS":   ("us",  "small", "Healthcare", "Medical Devices"),
    "ICUI":   ("us",  "small", "Healthcare", "Medical Instruments & Supplies"),
    # CAD — Large
    "CSH-UN.TO": ("tsx", "large", "Healthcare", "Medical Care Facilities"),
    # CAD — Mid
    "HHL.TO":    ("tsx", "mid",   "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "WELL.TO":   ("tsx", "mid",   "Healthcare", "Health Information Services"),
    "NHC.TO":    ("tsx", "mid",   "Healthcare", "Medical Care Facilities"),
    # CAD — Small
    "HHLE.TO":   ("tsx", "small", "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "HITI.TO":   ("tsx", "small", "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "TRUL.TO":   ("tsx", "small", "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "CURA.TO":   ("tsx", "small", "Healthcare", "Drug Manufacturers-Specialty & Generic"),

    # -- FINANCIAL SERVICES -------------------------------------------------------
    # US — Large
    "JPM":    ("us",  "large", "Financial Services", "Banks-Diversified"),
    "BAC":    ("us",  "large", "Financial Services", "Banks-Diversified"),
    "WFC":    ("us",  "large", "Financial Services", "Banks-Diversified"),
    "GS":     ("us",  "large", "Financial Services", "Capital Markets"),
    "MS":     ("us",  "large", "Financial Services", "Capital Markets"),
    "BLK":    ("us",  "large", "Financial Services", "Asset Management"),
    # US — Mid
    "EWBC":   ("us",  "mid",   "Financial Services", "Banks-Regional"),
    "WTFC":   ("us",  "mid",   "Financial Services", "Banks-Regional"),
    "PNFP":   ("us",  "mid",   "Financial Services", "Banks-Regional"),
    "FULT":   ("us",  "mid",   "Financial Services", "Banks-Regional"),
    "SFNC":   ("us",  "mid",   "Financial Services", "Banks-Regional"),
    "PFG":    ("us",  "mid",   "Financial Services", "Insurance-Life"),
    "RLI":    ("us",  "mid",   "Financial Services", "Insurance-Property & Casualty"),
    # US — Small
    "FFIN":   ("us",  "small", "Financial Services", "Banks-Regional"),
    "TOWN":   ("us",  "small", "Financial Services", "Banks-Regional"),
    "OCFC":   ("us",  "small", "Financial Services", "Banks-Regional"),
    "HTLF":   ("us",  "small", "Financial Services", "Banks-Regional"),
    "COOP":   ("us",  "small", "Financial Services", "Mortgage Finance"),
    # CAD — Large  (FIZZ intentionally omitted — belongs in Consumer Defensive)
    "RY.TO":   ("tsx", "large", "Financial Services", "Banks-Diversified"),
    "TD.TO":   ("tsx", "large", "Financial Services", "Banks-Diversified"),
    "BMO.TO":  ("tsx", "large", "Financial Services", "Banks-Diversified"),
    "CM.TO":   ("tsx", "large", "Financial Services", "Banks-Diversified"),
    "NA.TO":   ("tsx", "large", "Financial Services", "Banks-Diversified"),
    "MFC.TO":  ("tsx", "large", "Financial Services", "Insurance-Life"),
    # CAD — Mid
    "GWO.TO":  ("tsx", "mid",   "Financial Services", "Insurance-Life"),
    "IAG.TO":  ("tsx", "mid",   "Financial Services", "Insurance-Life"),
    "IFC.TO":  ("tsx", "mid",   "Financial Services", "Insurance-Property & Casualty"),
    "SLF.TO":  ("tsx", "mid",   "Financial Services", "Insurance-Life"),
    "EQB.TO":  ("tsx", "mid",   "Financial Services", "Banks-Regional"),
    "CWB.TO":  ("tsx", "mid",   "Financial Services", "Banks-Regional"),
    # CAD — Small
    "FSZ.TO":  ("tsx", "small", "Financial Services", "Asset Management"),
    "POW.TO":  ("tsx", "small", "Financial Services", "Asset Management"),
    "IGM.TO":  ("tsx", "small", "Financial Services", "Asset Management"),
    "ONEX.TO": ("tsx", "small", "Financial Services", "Asset Management"),
    "CIX.TO":  ("tsx", "small", "Financial Services", "Asset Management"),
    "BN.TO":   ("tsx", "small", "Financial Services", "Asset Management"),

    # -- TECHNOLOGY ---------------------------------------------------------------
    # US — Large
    "MSFT":   ("us",  "large", "Technology", "Software-Application"),
    "NVDA":   ("us",  "large", "Technology", "Semiconductors"),
    "AAPL":   ("us",  "large", "Technology", "Consumer Electronics"),
    "AVGO":   ("us",  "large", "Technology", "Semiconductors"),
    "ORCL":   ("us",  "large", "Technology", "Software-Application"),
    "AMD":    ("us",  "large", "Technology", "Semiconductors"),
    "INTC":   ("us",  "large", "Technology", "Semiconductors"),
    # US — Mid
    "FTNT":   ("us",  "mid",   "Technology", "Software-Infrastructure"),
    "PANW":   ("us",  "mid",   "Technology", "Software-Infrastructure"),
    "DDOG":   ("us",  "mid",   "Technology", "Software-Infrastructure"),
    "SNOW":   ("us",  "mid",   "Technology", "Software-Infrastructure"),
    "NET":    ("us",  "mid",   "Technology", "Software-Infrastructure"),
    "PLTR":   ("us",  "mid",   "Technology", "Software-Application"),
    "CRWD":   ("us",  "mid",   "Technology", "Software-Infrastructure"),
    # US — Small
    "AMBA":   ("us",  "small", "Technology", "Semiconductors"),
    "SMTC":   ("us",  "small", "Technology", "Semiconductors"),
    "PAYO":   ("us",  "small", "Technology", "Software-Application"),
    "TASK":   ("us",  "small", "Technology", "Software-Application"),
    "RELY":   ("us",  "small", "Technology", "Software-Application"),
    "ALKT":   ("us",  "small", "Technology", "Software-Application"),
    # CAD — Large
    "CSU.TO":  ("tsx", "large", "Technology", "Software-Application"),
    "SHOP.TO": ("tsx", "large", "Technology", "Software-Application"),
    "CGI.TO":  ("tsx", "large", "Technology", "Information Technology Services"),
    "DSG.TO":  ("tsx", "large", "Technology", "Software-Application"),
    # CAD — Mid
    "ENGH.TO": ("tsx", "mid",   "Technology", "Software-Application"),
    "KXS.TO":  ("tsx", "mid",   "Technology", "Software-Application"),
    "DCBO.TO": ("tsx", "mid",   "Technology", "Software-Application"),
    "LSPD.TO": ("tsx", "mid",   "Technology", "Software-Application"),
    "TIXT.TO": ("tsx", "mid",   "Technology", "Information Technology Services"),
    # CAD — Small
    "REAL.TO": ("tsx", "small", "Technology", "Software-Application"),
    "QTRH.TO": ("tsx", "small", "Technology", "Software-Application"),
    "TOI.TO":  ("tsx", "small", "Technology", "Software-Application"),
    "MTRX.TO": ("tsx", "small", "Technology", "Information Technology Services"),
    "BB.TO":   ("tsx", "small", "Technology", "Software-Application"),

    # -- COMMUNICATION SERVICES ---------------------------------------------------
    # US — Large
    "GOOG":   ("us",  "large", "Communication Services", "Internet Content & Information"),
    "META":   ("us",  "large", "Communication Services", "Internet Content & Information"),
    "NFLX":   ("us",  "large", "Communication Services", "Entertainment"),
    "DIS":    ("us",  "large", "Communication Services", "Entertainment"),
    "T":      ("us",  "large", "Communication Services", "Telecom Services"),
    "VZ":     ("us",  "large", "Communication Services", "Telecom Services"),
    # US — Mid
    "CHTR":   ("us",  "mid",   "Communication Services", "Pay TV"),
    "WBD":    ("us",  "mid",   "Communication Services", "Entertainment"),
    "NXST":   ("us",  "mid",   "Communication Services", "Broadcasting"),
    "LUMN":   ("us",  "mid",   "Communication Services", "Telecom Services"),
    "SIRI":   ("us",  "mid",   "Communication Services", "Broadcasting"),
    "TTWO":   ("us",  "mid",   "Communication Services", "Electronic Gaming & Multimedia"),
    # US — Small
    "CARG":   ("us",  "small", "Communication Services", "Internet Content & Information"),
    "GTN":    ("us",  "small", "Communication Services", "Broadcasting"),
    "SSP":    ("us",  "small", "Communication Services", "Broadcasting"),
    "IHRT":   ("us",  "small", "Communication Services", "Broadcasting"),
    "AMCX":   ("us",  "small", "Communication Services", "Entertainment"),
    # CAD — Large
    "BCE.TO":   ("tsx", "large", "Communication Services", "Telecom Services"),
    "T.TO":     ("tsx", "large", "Communication Services", "Telecom Services"),
    "RCI-B.TO": ("tsx", "large", "Communication Services", "Telecom Services"),
    "QBR-A.TO": ("tsx", "large", "Communication Services", "Telecom Services"),
    # CAD — Mid
    "MBT.TO":   ("tsx", "mid",   "Communication Services", "Telecom Services"),
    "CJR-B.TO": ("tsx", "mid",   "Communication Services", "Broadcasting"),
    "CORUS.TO": ("tsx", "mid",   "Communication Services", "Broadcasting"),
    "TVA-B.TO": ("tsx", "mid",   "Communication Services", "Broadcasting"),
    # CAD — Small
    "HHIS.TO":  ("tsx", "small", "Communication Services", "Telecom Services"),
    "DHX.TO":   ("tsx", "small", "Communication Services", "Entertainment"),
    "OUC.TO":   ("tsx", "small", "Communication Services", "Telecom Services"),

    # -- UTILITIES ----------------------------------------------------------------
    # US — Large
    "NEE":    ("us",  "large", "Utilities", "Utilities-Renewable"),
    "DUK":    ("us",  "large", "Utilities", "Utilities-Regulated Electric"),
    "SO":     ("us",  "large", "Utilities", "Utilities-Regulated Electric"),
    "AEP":    ("us",  "large", "Utilities", "Utilities-Regulated Electric"),
    "EXC":    ("us",  "large", "Utilities", "Utilities-Regulated Electric"),
    "D":      ("us",  "large", "Utilities", "Utilities-Regulated Electric"),
    # US — Mid
    "AES":    ("us",  "mid",   "Utilities", "Utilities-Diversified"),
    "NRG":    ("us",  "mid",   "Utilities", "Utilities-Independent Power Producers"),
    "WTRG":   ("us",  "mid",   "Utilities", "Utilities-Regulated Water"),
    "AWK":    ("us",  "mid",   "Utilities", "Utilities-Regulated Water"),
    "OGE":    ("us",  "mid",   "Utilities", "Utilities-Regulated Electric"),
    "NI":     ("us",  "mid",   "Utilities", "Utilities-Regulated Gas"),
    # US — Small
    "YORW":   ("us",  "small", "Utilities", "Utilities-Regulated Water"),
    "ARTNA":  ("us",  "small", "Utilities", "Utilities-Regulated Water"),
    "MSEX":   ("us",  "small", "Utilities", "Utilities-Regulated Water"),
    "CWCO":   ("us",  "small", "Utilities", "Utilities-Regulated Water"),
    "GWRS":   ("us",  "small", "Utilities", "Utilities-Regulated Water"),
    # CAD — Large
    "FTS.TO":  ("tsx", "large", "Utilities", "Utilities-Regulated Electric"),
    "H.TO":    ("tsx", "large", "Utilities", "Utilities-Regulated Electric"),
    "EMA.TO":  ("tsx", "large", "Utilities", "Utilities-Regulated Electric"),
    "CU.TO":   ("tsx", "large", "Utilities", "Utilities-Regulated Electric"),
    # CAD — Mid
    "AQN.TO":   ("tsx", "mid",   "Utilities", "Utilities-Renewable"),
    "NPI.TO":   ("tsx", "mid",   "Utilities", "Utilities-Renewable"),
    "BLX.TO":   ("tsx", "mid",   "Utilities", "Utilities-Renewable"),
    "CPX.TO":   ("tsx", "mid",   "Utilities", "Utilities-Independent Power Producers"),
    "ACO-X.TO": ("tsx", "mid",   "Utilities", "Utilities-Regulated Electric"),
    # CAD — Small
    "WTE.TO":   ("tsx", "small", "Utilities", "Utilities-Renewable"),
    "INE.TO":   ("tsx", "small", "Utilities", "Utilities-Renewable"),
    "PHX.TO":   ("tsx", "small", "Utilities", "Utilities-Regulated Gas"),
    "AW-UN.TO": ("tsx", "small", "Utilities", "Utilities-Regulated Water"),

    # -- REAL ESTATE --------------------------------------------------------------
    # US — Large
    "PLD":    ("us",  "large", "Real Estate", "REIT-Industrial"),
    "AMT":    ("us",  "large", "Real Estate", "REIT-Specialty"),
    "EQIX":   ("us",  "large", "Real Estate", "REIT-Specialty"),
    "PSA":    ("us",  "large", "Real Estate", "REIT-Specialty"),
    "O":      ("us",  "large", "Real Estate", "REIT-Retail"),
    # US — Mid
    "VICI":   ("us",  "mid",   "Real Estate", "REIT-Diversified"),
    "EXR":    ("us",  "mid",   "Real Estate", "REIT-Specialty"),
    "MAA":    ("us",  "mid",   "Real Estate", "REIT-Residential"),
    "NNN":    ("us",  "mid",   "Real Estate", "REIT-Retail"),
    "GLPI":   ("us",  "mid",   "Real Estate", "REIT-Diversified"),
    "ARE":    ("us",  "mid",   "Real Estate", "REIT-Office"),
    # US — Small
    "SBRA":   ("us",  "small", "Real Estate", "REIT-Healthcare Facilities"),
    "UHT":    ("us",  "small", "Real Estate", "REIT-Healthcare Facilities"),
    "HIW":    ("us",  "small", "Real Estate", "REIT-Office"),
    "MPT":    ("us",  "small", "Real Estate", "REIT-Healthcare Facilities"),
    "LAND":   ("us",  "small", "Real Estate", "REIT-Diversified"),
    # CAD — Large
    "REI-UN.TO": ("tsx", "large", "Real Estate", "REIT-Retail"),
    "CAR-UN.TO": ("tsx", "large", "Real Estate", "REIT-Residential"),
    "AP-UN.TO":  ("tsx", "large", "Real Estate", "REIT-Industrial"),
    "SRU-UN.TO": ("tsx", "large", "Real Estate", "REIT-Retail"),
    "GRT-UN.TO": ("tsx", "large", "Real Estate", "REIT-Industrial"),
    # CAD — Mid
    "HR-UN.TO":  ("tsx", "mid",   "Real Estate", "REIT-Diversified"),
    "FCR-UN.TO": ("tsx", "mid",   "Real Estate", "REIT-Retail"),
    "KMP-UN.TO": ("tsx", "mid",   "Real Estate", "REIT-Industrial"),
    "IIP-UN.TO": ("tsx", "mid",   "Real Estate", "REIT-Residential"),
    "D-UN.TO":   ("tsx", "mid",   "Real Estate", "REIT-Office"),
    "CRR-UN.TO": ("tsx", "mid",   "Real Estate", "REIT-Industrial"),
    # CAD — Small
    "MRG-UN.TO":  ("tsx", "small", "Real Estate", "REIT-Residential"),
    "NRR-UN.TO":  ("tsx", "small", "Real Estate", "REIT-Industrial"),
    "SGR-UN.TO":  ("tsx", "small", "Real Estate", "REIT-Retail"),
    "HOM-UN.TO":  ("tsx", "small", "Real Estate", "REIT-Residential"),
    "MI-UN.TO":   ("tsx", "small", "Real Estate", "REIT-Diversified"),
    "VITL-UN.TO": ("tsx", "small", "Real Estate", "REIT-Healthcare Facilities"),

    # ── TECHNOLOGY (additional) ─────────────────────────────────────────────────
    # US — Large
    "QCOM":  ("us", "large", "Technology", "Semiconductors"),
    "TXN":   ("us", "large", "Technology", "Semiconductors"),
    "MU":    ("us", "large", "Technology", "Semiconductors"),
    "AMAT":  ("us", "large", "Technology", "Semiconductor Equipment & Materials"),
    "KLAC":  ("us", "large", "Technology", "Semiconductor Equipment & Materials"),
    "MRVL":  ("us", "large", "Technology", "Semiconductors"),
    "NXPI":  ("us", "large", "Technology", "Semiconductors"),
    "ADI":   ("us", "large", "Technology", "Semiconductors"),
    "ACN":   ("us", "large", "Technology", "Information Technology Services"),
    "IBM":   ("us", "large", "Technology", "Information Technology Services"),
    "HPQ":   ("us", "large", "Technology", "Computer Hardware"),
    "DELL":  ("us", "large", "Technology", "Computer Hardware"),
    "HPE":   ("us", "large", "Technology", "Information Technology Services"),
    "KEYS":  ("us", "large", "Technology", "Scientific & Technical Instruments"),
    "NOW":   ("us", "large", "Technology", "Software-Infrastructure"),
    "INTU":  ("us", "large", "Technology", "Software-Application"),
    "CDNS":  ("us", "large", "Technology", "Software-Application"),
    "ANSS":  ("us", "large", "Technology", "Software-Application"),
    "ADSK":  ("us", "large", "Technology", "Software-Application"),
    # US — Mid
    "ON":    ("us", "mid",   "Technology", "Semiconductors"),
    "ZS":    ("us", "mid",   "Technology", "Software-Infrastructure"),
    "OKTA":  ("us", "mid",   "Technology", "Software-Infrastructure"),
    "MDB":   ("us", "mid",   "Technology", "Software-Infrastructure"),
    "BILL":  ("us", "mid",   "Technology", "Software-Application"),
    "HUBS":  ("us", "mid",   "Technology", "Software-Application"),
    "ZI":    ("us", "mid",   "Technology", "Software-Application"),
    "PCTY":  ("us", "mid",   "Technology", "Software-Application"),
    "PAYC":  ("us", "mid",   "Technology", "Software-Application"),
    "TWLO":  ("us", "mid",   "Technology", "Communication Equipment"),
    "SMAR":  ("us", "mid",   "Technology", "Software-Application"),
    "EPAM":  ("us", "mid",   "Technology", "Information Technology Services"),
    "GLOB":  ("us", "mid",   "Technology", "Information Technology Services"),
    "DT":    ("us", "mid",   "Technology", "Software-Infrastructure"),
    "NTNX":  ("us", "mid",   "Technology", "Software-Infrastructure"),
    "SWKS":  ("us", "mid",   "Technology", "Semiconductors"),
    "QRVO":  ("us", "mid",   "Technology", "Semiconductors"),
    "FOUR":  ("us", "mid",   "Technology", "Software-Application"),
    "TOST":  ("us", "mid",   "Technology", "Software-Application"),
    "CTSH":  ("us", "mid",   "Technology", "Information Technology Services"),
    "PTC":   ("us", "mid",   "Technology", "Software-Application"),
    "WEX":   ("us", "mid",   "Technology", "Software-Application"),
    "EXLS":  ("us", "mid",   "Technology", "Information Technology Services"),
    "CNXC":  ("us", "mid",   "Technology", "Information Technology Services"),
    "PSTG":  ("us", "mid",   "Technology", "Software-Infrastructure"),
    "ESTC":  ("us", "mid",   "Technology", "Software-Infrastructure"),
    "WK":    ("us", "mid",   "Technology", "Software-Application"),
    "FIVN":  ("us", "mid",   "Technology", "Communication Equipment"),
    "YEXT":  ("us", "mid",   "Technology", "Software-Application"),
    "ASAN":  ("us", "mid",   "Technology", "Software-Application"),
    "RNG":   ("us", "mid",   "Technology", "Communication Equipment"),
    # US — Small
    "COHU":  ("us", "small", "Technology", "Semiconductor Equipment & Materials"),
    "KLIC":  ("us", "small", "Technology", "Semiconductor Equipment & Materials"),
    "ICHR":  ("us", "small", "Technology", "Semiconductor Equipment & Materials"),
    "ONTO":  ("us", "small", "Technology", "Semiconductor Equipment & Materials"),
    "BRKS":  ("us", "small", "Technology", "Semiconductor Equipment & Materials"),
    "CEVA":  ("us", "small", "Technology", "Semiconductors"),
    "SITM":  ("us", "small", "Technology", "Semiconductors"),
    "RMBS":  ("us", "small", "Technology", "Semiconductors"),
    "LSCC":  ("us", "small", "Technology", "Semiconductors"),
    "ALGM":  ("us", "small", "Technology", "Semiconductors"),
    "MTSI":  ("us", "small", "Technology", "Semiconductors"),
    "FORM":  ("us", "small", "Technology", "Semiconductor Equipment & Materials"),
    "ACMR":  ("us", "small", "Technology", "Semiconductor Equipment & Materials"),
    "AI":    ("us", "small", "Technology", "Software-Application"),
    "CRNC":  ("us", "small", "Technology", "Software-Application"),
    "SPSC":  ("us", "small", "Technology", "Software-Application"),
    "NTCT":  ("us", "small", "Technology", "Software-Infrastructure"),
    "EGHT":  ("us", "small", "Technology", "Communication Equipment"),
    "BAND":  ("us", "small", "Technology", "Communication Equipment"),
    "DOMO":  ("us", "small", "Technology", "Software-Application"),
    "ZUO":   ("us", "small", "Technology", "Software-Application"),
    "JAMF":  ("us", "small", "Technology", "Software-Application"),

    # ── HEALTHCARE (additional) ──────────────────────────────────────────────────
    # US — Large
    "SYK":   ("us", "large", "Healthcare", "Medical Devices"),
    "ZBH":   ("us", "large", "Healthcare", "Medical Devices"),
    "ISRG":  ("us", "large", "Healthcare", "Medical Devices"),
    "TMO":   ("us", "large", "Healthcare", "Diagnostics & Research"),
    "DHR":   ("us", "large", "Healthcare", "Diagnostics & Research"),
    "A":     ("us", "large", "Healthcare", "Diagnostics & Research"),
    "IQV":   ("us", "large", "Healthcare", "Diagnostics & Research"),
    "EW":    ("us", "large", "Healthcare", "Medical Devices"),
    "REGN":  ("us", "large", "Healthcare", "Drug Manufacturers-General"),
    "BIIB":  ("us", "large", "Healthcare", "Drug Manufacturers-General"),
    "VRTX":  ("us", "large", "Healthcare", "Drug Manufacturers-General"),
    "ILMN":  ("us", "large", "Healthcare", "Diagnostics & Research"),
    "RPRX":  ("us", "large", "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    # US — Mid
    "MTD":   ("us", "mid",   "Healthcare", "Diagnostics & Research"),
    "STE":   ("us", "mid",   "Healthcare", "Medical Instruments & Supplies"),
    "VEEV":  ("us", "mid",   "Healthcare", "Health Information Services"),
    "JAZZ":  ("us", "mid",   "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "HQY":   ("us", "mid",   "Healthcare", "Health Information Services"),
    "GKOS":  ("us", "mid",   "Healthcare", "Medical Devices"),
    "AXNX":  ("us", "mid",   "Healthcare", "Medical Devices"),
    "NVRO":  ("us", "mid",   "Healthcare", "Medical Devices"),
    "MMSI":  ("us", "mid",   "Healthcare", "Medical Instruments & Supplies"),
    "IRTC":  ("us", "mid",   "Healthcare", "Medical Devices"),
    "NTRA":  ("us", "mid",   "Healthcare", "Diagnostics & Research"),
    "DOCS":  ("us", "mid",   "Healthcare", "Health Information Services"),
    "PHR":   ("us", "mid",   "Healthcare", "Health Information Services"),
    "HOLX":  ("us", "mid",   "Healthcare", "Medical Instruments & Supplies"),
    "DGX":   ("us", "mid",   "Healthcare", "Diagnostics & Research"),
    "LH":    ("us", "mid",   "Healthcare", "Diagnostics & Research"),
    "WAT":   ("us", "mid",   "Healthcare", "Diagnostics & Research"),
    "RVTY":  ("us", "mid",   "Healthcare", "Scientific & Technical Instruments"),
    "ALNY":  ("us", "mid",   "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "INCY":  ("us", "mid",   "Healthcare", "Drug Manufacturers-Specialty & Generic"),
    "XRAY":  ("us", "mid",   "Healthcare", "Medical Instruments & Supplies"),
    "ALGN":  ("us", "mid",   "Healthcare", "Medical Devices"),
    "DXCM":  ("us", "mid",   "Healthcare", "Medical Devices"),
    # US — Small
    "RGEN":  ("us", "small", "Healthcare", "Biotechnology"),
    "LMAT":  ("us", "small", "Healthcare", "Medical Devices"),
    "MDXG":  ("us", "small", "Healthcare", "Medical Devices"),
    "OSUR":  ("us", "small", "Healthcare", "Medical Devices"),
    "NARI":  ("us", "small", "Healthcare", "Medical Devices"),
    "ATEC":  ("us", "small", "Healthcare", "Medical Devices"),
    "STAA":  ("us", "small", "Healthcare", "Medical Devices"),
    "NVST":  ("us", "small", "Healthcare", "Medical Devices"),
    "TCMD":  ("us", "small", "Healthcare", "Medical Devices"),
    "SRDX":  ("us", "small", "Healthcare", "Medical Instruments & Supplies"),
    "ACCD":  ("us", "small", "Healthcare", "Health Information Services"),

    # ── FINANCIAL SERVICES (additional) ─────────────────────────────────────────
    # US — Large
    "C":     ("us", "large", "Financial Services", "Banks-Diversified"),
    "BRK-B": ("us", "large", "Financial Services", "Insurance-Diversified"),
    "USB":   ("us", "large", "Financial Services", "Banks-Diversified"),
    "PNC":   ("us", "large", "Financial Services", "Banks-Diversified"),
    "AIG":   ("us", "large", "Financial Services", "Insurance-Diversified"),
    "MET":   ("us", "large", "Financial Services", "Insurance-Life"),
    "PRU":   ("us", "large", "Financial Services", "Insurance-Life"),
    "AFL":   ("us", "large", "Financial Services", "Insurance-Life"),
    "CB":    ("us", "large", "Financial Services", "Insurance-Property & Casualty"),
    "TRV":   ("us", "large", "Financial Services", "Insurance-Property & Casualty"),
    "ALL":   ("us", "large", "Financial Services", "Insurance-Property & Casualty"),
    "HIG":   ("us", "large", "Financial Services", "Insurance-Property & Casualty"),
    "MMC":   ("us", "large", "Financial Services", "Insurance Brokers"),
    "AON":   ("us", "large", "Financial Services", "Insurance Brokers"),
    "WTW":   ("us", "large", "Financial Services", "Insurance Brokers"),
    "CME":   ("us", "large", "Financial Services", "Financial Data & Stock Exchanges"),
    "ICE":   ("us", "large", "Financial Services", "Financial Data & Stock Exchanges"),
    "SPGI":  ("us", "large", "Financial Services", "Financial Data & Stock Exchanges"),
    "MCO":   ("us", "large", "Financial Services", "Financial Data & Stock Exchanges"),
    "MSCI":  ("us", "large", "Financial Services", "Financial Data & Stock Exchanges"),
    "BK":    ("us", "large", "Financial Services", "Asset Management"),
    "NTRS":  ("us", "large", "Financial Services", "Asset Management"),
    "STT":   ("us", "large", "Financial Services", "Asset Management"),
    "COF":   ("us", "large", "Financial Services", "Credit Services"),
    # US — Mid
    "CFG":   ("us", "mid",   "Financial Services", "Banks-Regional"),
    "HBAN":  ("us", "mid",   "Financial Services", "Banks-Regional"),
    "KEY":   ("us", "mid",   "Financial Services", "Banks-Regional"),
    "RF":    ("us", "mid",   "Financial Services", "Banks-Regional"),
    "FITB":  ("us", "mid",   "Financial Services", "Banks-Regional"),
    "MTB":   ("us", "mid",   "Financial Services", "Banks-Regional"),
    "ZION":  ("us", "mid",   "Financial Services", "Banks-Regional"),
    "SNV":   ("us", "mid",   "Financial Services", "Banks-Regional"),
    "CMA":   ("us", "mid",   "Financial Services", "Banks-Regional"),
    "FHN":   ("us", "mid",   "Financial Services", "Banks-Regional"),
    "SF":    ("us", "mid",   "Financial Services", "Capital Markets"),
    "LPLA":  ("us", "mid",   "Financial Services", "Capital Markets"),
    "RJF":   ("us", "mid",   "Financial Services", "Capital Markets"),
    "SEIC":  ("us", "mid",   "Financial Services", "Asset Management"),
    "SLM":   ("us", "mid",   "Financial Services", "Credit Services"),
    "EVR":   ("us", "mid",   "Financial Services", "Capital Markets"),
    "MKTX":  ("us", "mid",   "Financial Services", "Financial Data & Stock Exchanges"),
    "PJT":   ("us", "mid",   "Financial Services", "Capital Markets"),
    "VIRT":  ("us", "mid",   "Financial Services", "Capital Markets"),
    "AFG":   ("us", "mid",   "Financial Services", "Insurance-Property & Casualty"),
    "MKL":   ("us", "mid",   "Financial Services", "Insurance-Property & Casualty"),
    "RNR":   ("us", "mid",   "Financial Services", "Insurance-Property & Casualty"),
    "RYAN":  ("us", "mid",   "Financial Services", "Insurance Brokers"),
    "WRB":   ("us", "mid",   "Financial Services", "Insurance-Property & Casualty"),
    "ORI":   ("us", "mid",   "Financial Services", "Insurance-Diversified"),
    "SIGI":  ("us", "mid",   "Financial Services", "Insurance-Property & Casualty"),
    "KMPR":  ("us", "mid",   "Financial Services", "Insurance-Property & Casualty"),
    "CNO":   ("us", "mid",   "Financial Services", "Insurance-Life"),
    "WSBC":  ("us", "mid",   "Financial Services", "Banks-Regional"),
    # US — Small  (override spec's Industrials misclassification above)
    "ABCB":  ("us", "small", "Financial Services", "Banks-Regional"),
    "BANR":  ("us", "small", "Financial Services", "Banks-Regional"),
    "CBSH":  ("us", "small", "Financial Services", "Banks-Regional"),
    "COLB":  ("us", "small", "Financial Services", "Banks-Regional"),
    "INDB":  ("us", "small", "Financial Services", "Banks-Regional"),
    "LKFN":  ("us", "small", "Financial Services", "Banks-Regional"),
    "UMBF":  ("us", "small", "Financial Services", "Banks-Regional"),
    "WAFD":  ("us", "small", "Financial Services", "Banks-Regional"),
    "WSFS":  ("us", "small", "Financial Services", "Banks-Regional"),
    "BPOP":  ("us", "small", "Financial Services", "Banks-Regional"),
    "CADE":  ("us", "small", "Financial Services", "Banks-Regional"),
    "FNB":   ("us", "small", "Financial Services", "Banks-Regional"),
    "HWC":   ("us", "small", "Financial Services", "Banks-Regional"),
    "TRMK":  ("us", "small", "Financial Services", "Banks-Regional"),
    "SASR":  ("us", "small", "Financial Services", "Banks-Regional"),
    "SBCF":  ("us", "small", "Financial Services", "Banks-Regional"),
    "SFBS":  ("us", "small", "Financial Services", "Banks-Regional"),
    "UCB":   ("us", "small", "Financial Services", "Banks-Regional"),
    "EFSC":  ("us", "small", "Financial Services", "Banks-Regional"),
    "HAFC":  ("us", "small", "Financial Services", "Banks-Regional"),
    "HMST":  ("us", "small", "Financial Services", "Banks-Regional"),
    "QCRH":  ("us", "small", "Financial Services", "Banks-Regional"),
    "STBA":  ("us", "small", "Financial Services", "Banks-Regional"),

    # ── CONSUMER CYCLICAL (additional) ──────────────────────────────────────────
    # US — Large
    "BKNG":  ("us", "large", "Consumer Cyclical", "Travel Services"),
    "MAR":   ("us", "large", "Consumer Cyclical", "Lodging"),
    "HLT":   ("us", "large", "Consumer Cyclical", "Lodging"),
    "DRI":   ("us", "large", "Consumer Cyclical", "Restaurants"),
    "RCL":   ("us", "large", "Consumer Cyclical", "Travel Services"),
    "EXPE":  ("us", "large", "Consumer Cyclical", "Travel Services"),
    "ABNB":  ("us", "large", "Consumer Cyclical", "Travel Services"),
    "UBER":  ("us", "large", "Consumer Cyclical", "Personal Services"),
    "RL":    ("us", "large", "Consumer Cyclical", "Apparel Manufacturing"),
    "WSM":   ("us", "large", "Consumer Cyclical", "Home Furnishings & Fixtures"),
    # US — Mid
    "QSR":   ("us", "mid",   "Consumer Cyclical", "Restaurants"),
    "TXRH":  ("us", "mid",   "Consumer Cyclical", "Restaurants"),
    "WING":  ("us", "mid",   "Consumer Cyclical", "Restaurants"),
    "EAT":   ("us", "mid",   "Consumer Cyclical", "Restaurants"),
    "BLMN":  ("us", "mid",   "Consumer Cyclical", "Restaurants"),
    "CAKE":  ("us", "mid",   "Consumer Cyclical", "Restaurants"),
    "PLAY":  ("us", "mid",   "Consumer Cyclical", "Leisure"),
    "WH":    ("us", "mid",   "Consumer Cyclical", "Lodging"),
    # US — Small
    "BJRI":  ("us", "small", "Consumer Cyclical", "Restaurants"),
    "RRGB":  ("us", "small", "Consumer Cyclical", "Restaurants"),
    # CAD — Mid
    "ATZ.TO": ("tsx", "mid",  "Consumer Cyclical", "Apparel Retail"),

    # ── CONSUMER DEFENSIVE (additional) ─────────────────────────────────────────
    # US — Large
    "MO":    ("us", "large", "Consumer Defensive", "Tobacco"),
    # US — Mid
    "GO":    ("us", "mid",   "Consumer Defensive", "Grocery Stores"),
    "USFD":  ("us", "mid",   "Consumer Defensive", "Food Distribution"),
    "PPC":   ("us", "mid",   "Consumer Defensive", "Packaged Foods"),
    # US — Small
    "LANC":  ("us", "small", "Consumer Defensive", "Packaged Foods"),
    "COKE":  ("us", "small", "Consumer Defensive", "Beverages-Non-Alcoholic"),
    "NATH":  ("us", "small", "Consumer Defensive", "Packaged Foods"),
    "HAIN":  ("us", "small", "Consumer Defensive", "Packaged Foods"),
    "FRPT":  ("us", "small", "Consumer Defensive", "Packaged Foods"),
    "CENT":  ("us", "small", "Consumer Defensive", "Specialty Chemicals"),
    # CAD — Large
    "WN.TO": ("tsx", "large", "Consumer Defensive", "Grocery Stores"),
    # CAD — Small
    "ADW-A.TO": ("tsx", "small", "Consumer Defensive", "Beverages-Wineries & Distilleries"),

    # ── INDUSTRIALS (additional) ─────────────────────────────────────────────────
    # US — Large
    "MMM":   ("us", "large", "Industrials", "Diversified Industrials"),
    "ETN":   ("us", "large", "Industrials", "Electrical Components & Equipment"),
    "ITW":   ("us", "large", "Industrials", "Diversified Industrials"),
    "PH":    ("us", "large", "Industrials", "Diversified Industrials"),
    "DOV":   ("us", "large", "Industrials", "Diversified Industrials"),
    "GWW":   ("us", "large", "Industrials", "Industrial Distribution"),
    "FAST":  ("us", "large", "Industrials", "Industrial Distribution"),
    "AME":   ("us", "large", "Industrials", "Scientific & Technical Instruments"),
    "CARR":  ("us", "large", "Industrials", "Building Products & Equipment"),
    "OTIS":  ("us", "large", "Industrials", "Building Products & Equipment"),
    "FLR":   ("us", "large", "Industrials", "Engineering & Construction"),
    "J":     ("us", "large", "Industrials", "Engineering & Construction"),
    "AGCO":  ("us", "large", "Industrials", "Farm & Heavy Construction Machinery"),
    "TDY":   ("us", "large", "Industrials", "Scientific & Technical Instruments"),
    "URI":   ("us", "large", "Industrials", "Rental & Leasing Services"),
    "JBHT":  ("us", "large", "Industrials", "Integrated Freight & Logistics"),
    "SWK":   ("us", "large", "Industrials", "Tools & Accessories"),
    # US — Mid
    "HWM":   ("us", "mid",   "Industrials", "Aerospace & Defense"),
    "CW":    ("us", "mid",   "Industrials", "Aerospace & Defense"),
    "TXT":   ("us", "mid",   "Industrials", "Aerospace & Defense"),
    "BAH":   ("us", "mid",   "Industrials", "Consulting Services"),
    "DRS":   ("us", "mid",   "Industrials", "Aerospace & Defense"),
    "XPO":   ("us", "mid",   "Industrials", "Trucking"),
    "RXO":   ("us", "mid",   "Industrials", "Integrated Freight & Logistics"),
    "OSK":   ("us", "mid",   "Industrials", "Construction Machinery & Heavy Trucks"),
    "IEX":   ("us", "mid",   "Industrials", "Industrial Machinery"),
    "LII":   ("us", "mid",   "Industrials", "Building Products & Equipment"),
    "NDSN":  ("us", "mid",   "Industrials", "Industrial Machinery"),
    "NVT":   ("us", "mid",   "Industrials", "Electrical Components & Equipment"),
    "HUBB":  ("us", "mid",   "Industrials", "Electrical Components & Equipment"),
    "TRMB":  ("us", "mid",   "Industrials", "Scientific & Technical Instruments"),
    "BWXT":  ("us", "mid",   "Industrials", "Aerospace & Defense"),
    "CACI":  ("us", "mid",   "Industrials", "Consulting Services"),
    # US — Small
    "MWA":   ("us", "small", "Industrials", "Building Products & Equipment"),
    "AZZ":   ("us", "small", "Industrials", "Electrical Components & Equipment"),
    "ATRO":  ("us", "small", "Industrials", "Aerospace & Defense"),
    "DCI":   ("us", "small", "Industrials", "Industrial Machinery"),
    "LCII":  ("us", "small", "Industrials", "Recreational Vehicles"),
    "AMWD":  ("us", "small", "Industrials", "Building Products & Equipment"),
    "AEIS":  ("us", "small", "Industrials", "Electrical Components & Equipment"),
    "AWI":   ("us", "small", "Industrials", "Building Products & Equipment"),
    "AVAV":  ("us", "small", "Industrials", "Aerospace & Defense"),
    "NPO":   ("us", "small", "Industrials", "Industrial Machinery"),
    "AIR":   ("us", "small", "Industrials", "Aerospace & Defense"),
    # CAD — Large
    "ATRL.TO": ("tsx", "large", "Industrials", "Engineering & Construction"),
    # CAD — Mid
    "AC.TO":   ("tsx", "mid",   "Industrials", "Airlines"),
    "CJT.TO":  ("tsx", "mid",   "Industrials", "Air Freight & Logistics"),
    # CAD — Small
    "MTL.TO":  ("tsx", "small", "Industrials", "Trucking"),

    # ── ENERGY (additional) ──────────────────────────────────────────────────────
    # US — Large
    "WMB":   ("us", "large", "Energy", "Oil & Gas Storage & Transportation"),
    "OKE":   ("us", "large", "Energy", "Oil & Gas Storage & Transportation"),
    "LNG":   ("us", "large", "Energy", "Oil & Gas Storage & Transportation"),
    "MPC":   ("us", "large", "Energy", "Oil & Gas Refining & Marketing"),
    "VLO":   ("us", "large", "Energy", "Oil & Gas Refining & Marketing"),
    "HES":   ("us", "large", "Energy", "Oil & Gas E&P"),
    "FANG":  ("us", "large", "Energy", "Oil & Gas E&P"),
    "CTRA":  ("us", "large", "Energy", "Oil & Gas E&P"),
    # US — Mid
    "RRC":   ("us", "mid",   "Energy", "Oil & Gas E&P"),
    "SM":    ("us", "mid",   "Energy", "Oil & Gas E&P"),
    "GPOR":  ("us", "mid",   "Energy", "Oil & Gas E&P"),
    "MGY":   ("us", "mid",   "Energy", "Oil & Gas E&P"),
    "DINO":  ("us", "mid",   "Energy", "Oil & Gas Refining & Marketing"),
    "PBF":   ("us", "mid",   "Energy", "Oil & Gas Refining & Marketing"),
    "TRGP":  ("us", "mid",   "Energy", "Oil & Gas Storage & Transportation"),
    "MPLX":  ("us", "mid",   "Energy", "Oil & Gas Storage & Transportation"),
    "HP":    ("us", "mid",   "Energy", "Oil & Gas Equipment & Services"),
    "WHD":   ("us", "mid",   "Energy", "Oil & Gas Equipment & Services"),
    "APA":   ("us", "mid",   "Energy", "Oil & Gas E&P"),
    # US — Small
    "CRC":   ("us", "small", "Energy", "Oil & Gas E&P"),
    "PR":    ("us", "small", "Energy", "Oil & Gas E&P"),
    "RES":   ("us", "small", "Energy", "Oil & Gas Equipment & Services"),
    "SUN":   ("us", "small", "Energy", "Oil & Gas Storage & Transportation"),
    # CAD — Large
    "TOU.TO": ("tsx", "large", "Energy", "Oil & Gas E&P"),
    "PPL.TO": ("tsx", "large", "Energy", "Oil & Gas Storage & Transportation"),
    # CAD — Mid
    "ARX.TO": ("tsx", "mid",   "Energy", "Oil & Gas E&P"),
    "KEY.TO": ("tsx", "mid",   "Energy", "Oil & Gas Storage & Transportation"),
    "PSK.TO": ("tsx", "mid",   "Energy", "Oil & Gas E&P"),
    "CPG.TO": ("tsx", "mid",   "Energy", "Oil & Gas E&P"),
    "FRU.TO": ("tsx", "mid",   "Energy", "Oil & Gas E&P"),
    "PXT.TO": ("tsx", "mid",   "Energy", "Oil & Gas E&P"),
    # CAD — Small
    "NVA.TO":  ("tsx", "small", "Energy", "Oil & Gas E&P"),
    "AAV.TO":  ("tsx", "small", "Energy", "Oil & Gas E&P"),
    "ATH.TO":  ("tsx", "small", "Energy", "Oil & Gas E&P"),
    "STEP.TO": ("tsx", "small", "Energy", "Oil & Gas Equipment & Services"),
    "CEU.TO":  ("tsx", "small", "Energy", "Oil & Gas Equipment & Services"),
    "PD.TO":   ("tsx", "small", "Energy", "Oil & Gas Equipment & Services"),
    "RRX.TO":  ("tsx", "small", "Energy", "Oil & Gas Equipment & Services"),

    # ── BASIC MATERIALS (additional) ────────────────────────────────────────────
    # US — Large
    "PPG":   ("us", "large", "Basic Materials", "Specialty Chemicals"),
    "SHW":   ("us", "large", "Basic Materials", "Specialty Chemicals"),
    "CF":    ("us", "large", "Basic Materials", "Agricultural Inputs"),
    "DOW":   ("us", "large", "Basic Materials", "Specialty Chemicals"),
    "MLM":   ("us", "large", "Basic Materials", "Building Materials"),
    "VMC":   ("us", "large", "Basic Materials", "Building Materials"),
    # US — Mid
    "BALL":  ("us", "mid",   "Basic Materials", "Packaging & Containers"),
    "SON":   ("us", "mid",   "Basic Materials", "Packaging & Containers"),
    "SEE":   ("us", "mid",   "Basic Materials", "Packaging & Containers"),
    "PKG":   ("us", "mid",   "Basic Materials", "Paper & Paper Products"),
    "RPM":   ("us", "mid",   "Basic Materials", "Specialty Chemicals"),
    "OLN":   ("us", "mid",   "Basic Materials", "Specialty Chemicals"),
    "ZEUS":  ("us", "mid",   "Basic Materials", "Steel"),
    "ATI":   ("us", "mid",   "Basic Materials", "Steel"),
    "WOR":   ("us", "mid",   "Basic Materials", "Steel"),
    "CSTM":  ("us", "mid",   "Basic Materials", "Aluminum"),
    "AXTA":  ("us", "mid",   "Basic Materials", "Specialty Chemicals"),
    "EXP":   ("us", "mid",   "Basic Materials", "Building Materials"),
    "UFPI":  ("us", "mid",   "Basic Materials", "Lumber & Wood Production"),
    "HXL":   ("us", "mid",   "Basic Materials", "Specialty Chemicals"),
    "SUM":   ("us", "mid",   "Basic Materials", "Building Materials"),
    # US — Small
    "BCPC":  ("us", "small", "Basic Materials", "Specialty Chemicals"),
    "OEC":   ("us", "small", "Basic Materials", "Specialty Chemicals"),
    "IOSP":  ("us", "small", "Basic Materials", "Specialty Chemicals"),
    # CAD — Large
    "CCO.TO": ("tsx", "large", "Basic Materials", "Uranium"),
    # CAD — Mid
    "AGI.TO": ("tsx", "mid",   "Basic Materials", "Gold"),
    "FR.TO":  ("tsx", "mid",   "Basic Materials", "Silver"),
    "IFP.TO": ("tsx", "mid",   "Basic Materials", "Lumber & Wood Production"),
    "CFP.TO": ("tsx", "mid",   "Basic Materials", "Lumber & Wood Production"),
    # CAD — Small
    "DPM.TO": ("tsx", "small", "Basic Materials", "Gold"),
    "ALS.TO": ("tsx", "small", "Basic Materials", "Other Industrial Metals & Mining"),
    "GCM.TO": ("tsx", "small", "Basic Materials", "Gold"),

    # ── REAL ESTATE (additional) ─────────────────────────────────────────────────
    # US — Large
    "CCI":   ("us", "large", "Real Estate", "REIT-Specialty"),
    "SBAC":  ("us", "large", "Real Estate", "REIT-Specialty"),
    "SPG":   ("us", "large", "Real Estate", "REIT-Retail"),
    "AVB":   ("us", "large", "Real Estate", "REIT-Residential"),
    "EQR":   ("us", "large", "Real Estate", "REIT-Residential"),
    "DLR":   ("us", "large", "Real Estate", "REIT-Specialty"),
    # US — Mid
    "BXP":   ("us", "mid",   "Real Estate", "REIT-Office"),
    "KIM":   ("us", "mid",   "Real Estate", "REIT-Retail"),
    "REG":   ("us", "mid",   "Real Estate", "REIT-Retail"),
    "ESS":   ("us", "mid",   "Real Estate", "REIT-Residential"),
    "CPT":   ("us", "mid",   "Real Estate", "REIT-Residential"),
    "UDR":   ("us", "mid",   "Real Estate", "REIT-Residential"),
    "INVH":  ("us", "mid",   "Real Estate", "REIT-Residential"),
    "IRT":   ("us", "mid",   "Real Estate", "REIT-Residential"),
    "REXR":  ("us", "mid",   "Real Estate", "REIT-Industrial"),
    "STAG":  ("us", "mid",   "Real Estate", "REIT-Industrial"),
    "TRNO":  ("us", "mid",   "Real Estate", "REIT-Industrial"),
    "IRM":   ("us", "mid",   "Real Estate", "REIT-Specialty"),
    "PEAK":  ("us", "mid",   "Real Estate", "REIT-Healthcare Facilities"),
    "VNO":   ("us", "mid",   "Real Estate", "REIT-Office"),
    # US — Small
    "IIPR":  ("us", "small", "Real Estate", "REIT-Industrial"),
    "GOOD":  ("us", "small", "Real Estate", "REIT-Diversified"),
    "PINE":  ("us", "small", "Real Estate", "REIT-Retail"),
    "LTC":   ("us", "small", "Real Estate", "REIT-Healthcare Facilities"),
    "AIRC":  ("us", "small", "Real Estate", "REIT-Residential"),
    "AIV":   ("us", "small", "Real Estate", "REIT-Residential"),
    "BDN":   ("us", "small", "Real Estate", "REIT-Office"),
    "EGP":   ("us", "small", "Real Estate", "REIT-Industrial"),
    "NTST":  ("us", "small", "Real Estate", "REIT-Retail"),
    "RYN":   ("us", "small", "Real Estate", "REIT-Specialty"),
    "SKT":   ("us", "small", "Real Estate", "REIT-Retail"),
    "UE":    ("us", "small", "Real Estate", "REIT-Retail"),
    "KRC":   ("us", "small", "Real Estate", "REIT-Office"),
    "SHO":   ("us", "small", "Real Estate", "REIT-Hotel & Motel"),
    "RHP":   ("us", "small", "Real Estate", "REIT-Hotel & Motel"),
    # CAD — Mid
    "DIR-UN.TO": ("tsx", "mid",   "Real Estate", "REIT-Industrial"),
    "CHP-UN.TO": ("tsx", "mid",   "Real Estate", "REIT-Retail"),
    "CIGI.TO":   ("tsx", "mid",   "Real Estate", "Real Estate Services"),
    # CAD — Small
    "NWH-UN.TO": ("tsx", "small", "Real Estate", "REIT-Healthcare Facilities"),
    "PLZ-UN.TO": ("tsx", "small", "Real Estate", "REIT-Retail"),
    "TNT-UN.TO": ("tsx", "small", "Real Estate", "REIT-Office"),
    "BTB-UN.TO": ("tsx", "small", "Real Estate", "REIT-Diversified"),
    "BSR-UN.TO": ("tsx", "small", "Real Estate", "REIT-Residential"),
    "DRM.TO":    ("tsx", "small", "Real Estate", "Real Estate Services"),
    "SIA.TO":    ("tsx", "small", "Real Estate", "REIT-Healthcare Facilities"),

    # ── UTILITIES (additional) ───────────────────────────────────────────────────
    # US — Large
    "SRE":   ("us", "large", "Utilities", "Utilities-Diversified"),
    "PCG":   ("us", "large", "Utilities", "Utilities-Regulated Electric"),
    "ED":    ("us", "large", "Utilities", "Utilities-Regulated Electric"),
    "WEC":   ("us", "large", "Utilities", "Utilities-Regulated Electric"),
    "XEL":   ("us", "large", "Utilities", "Utilities-Regulated Electric"),
    "PPL":   ("us", "large", "Utilities", "Utilities-Regulated Electric"),
    "CMS":   ("us", "large", "Utilities", "Utilities-Regulated Electric"),
    "DTE":   ("us", "large", "Utilities", "Utilities-Diversified"),
    "AEE":   ("us", "large", "Utilities", "Utilities-Regulated Electric"),
    "LNT":   ("us", "large", "Utilities", "Utilities-Regulated Electric"),
    "CNP":   ("us", "large", "Utilities", "Utilities-Regulated Electric"),
    # US — Mid
    "EVRG":  ("us", "mid",   "Utilities", "Utilities-Regulated Electric"),
    "SR":    ("us", "mid",   "Utilities", "Utilities-Regulated Gas"),
    "PNW":   ("us", "mid",   "Utilities", "Utilities-Regulated Electric"),
    # US — Small
    "IDA":   ("us", "small", "Utilities", "Utilities-Regulated Electric"),
    "CTWS":  ("us", "small", "Utilities", "Utilities-Regulated Water"),
    # CAD — Mid
    "TA.TO":  ("tsx", "mid",   "Utilities", "Utilities-Independent Power Producers"),
    "ALA.TO": ("tsx", "mid",   "Utilities", "Utilities-Diversified"),
    "ACI.TO": ("tsx", "mid",   "Utilities", "Utilities-Diversified"),

    # ── COMMUNICATION SERVICES (additional) ──────────────────────────────────────
    # US — Large
    "CMCSA":  ("us", "large", "Communication Services", "Pay TV"),
    "TMUS":   ("us", "large", "Communication Services", "Telecom Services"),
    "GOOGL":  ("us", "large", "Communication Services", "Internet Content & Information"),
    # US — Mid
    "EA":     ("us", "mid",   "Communication Services", "Electronic Gaming & Multimedia"),
    "MTCH":   ("us", "mid",   "Communication Services", "Internet Content & Information"),
    "FOX":    ("us", "mid",   "Communication Services", "Broadcasting"),
    "FOXA":   ("us", "mid",   "Communication Services", "Broadcasting"),
    "DISH":   ("us", "mid",   "Communication Services", "Pay TV"),
    "NWSA":   ("us", "mid",   "Communication Services", "Publishing"),
    # US — Small
    "IAC":    ("us", "small", "Communication Services", "Internet Content & Information"),
    "IRDM":   ("us", "small", "Communication Services", "Telecom Services"),
    "IDT":    ("us", "small", "Communication Services", "Telecom Services"),
    "USM":    ("us", "small", "Communication Services", "Telecom Services"),
    "SHEN":   ("us", "small", "Communication Services", "Telecom Services"),

    # ── FINANCIAL SERVICES — CAD (additional) ────────────────────────────────────
    "FFH.TO": ("tsx", "large", "Financial Services", "Insurance-Property & Casualty"),

    # ── TECHNOLOGY — CAD (additional) ────────────────────────────────────────────
    "DND.TO":  ("tsx", "small", "Technology", "Software-Application"),
    "ALYA.TO": ("tsx", "small", "Technology", "Information Technology Services"),
    "MDF.TO":  ("tsx", "small", "Technology", "Software-Application"),

    # ── HEALTHCARE — CAD (additional) ────────────────────────────────────────────
    "ACB.TO":  ("tsx", "small", "Healthcare", "Drug Manufacturers-Specialty & Generic"),
}
# fmt: on

UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "config" / "ticker_universe.json"


def migrate_existing_entry(entry: dict) -> dict:
    """Migrate v1 schema to v2: rename categories → signal_categories, add new fields."""
    if "categories" in entry and "signal_categories" not in entry:
        entry["signal_categories"] = entry.pop("categories")
    entry.setdefault("signal_categories", [])
    entry.setdefault("cap_tier", None)
    entry.setdefault("eodhd_sector", None)
    entry.setdefault("eodhd_industry", None)
    return entry


def build_new_entry(exchange: str, cap_tier: str, eodhd_sector: str, eodhd_industry: str) -> dict:
    return {
        "exchange": exchange,
        "has_data": True,
        "cap_tier": cap_tier,
        "eodhd_sector": eodhd_sector,
        "eodhd_industry": eodhd_industry,
        "signal_categories": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand ticker_universe.json to ~2,500 tickers.")
    parser.add_argument("--write", action="store_true", help="Write changes to disk")
    parser.add_argument("--backup", action="store_true", help="Backup existing file before write")
    args = parser.parse_args()

    with open(UNIVERSE_PATH) as f:
        universe = json.load(f)

    tickers: dict = universe["tickers"]

    # Migrate all existing entries to v2 schema
    for ticker in list(tickers.keys()):
        tickers[ticker] = migrate_existing_entry(tickers[ticker])

    added: list[str] = []
    backfilled: list[str] = []

    for ticker, (exchange, cap_tier, eodhd_sector, eodhd_industry) in NEW_TICKERS.items():
        if ticker in tickers:
            existing = tickers[ticker]
            changed = False
            if existing.get("cap_tier") is None:
                existing["cap_tier"] = cap_tier
                changed = True
            if existing.get("eodhd_sector") is None:
                existing["eodhd_sector"] = eodhd_sector
                changed = True
            if existing.get("eodhd_industry") is None:
                existing["eodhd_industry"] = eodhd_industry
                changed = True
            if changed:
                backfilled.append(ticker)
        else:
            tickers[ticker] = build_new_entry(exchange, cap_tier, eodhd_sector, eodhd_industry)
            added.append(ticker)

    us = sum(1 for t in tickers.values() if t["exchange"] == "us")
    tsx = sum(1 for t in tickers.values() if t["exchange"] == "tsx")
    tsxv = sum(1 for t in tickers.values() if t["exchange"] == "tsx_venture")
    no_data = sum(1 for t in tickers.values() if not t["has_data"])

    universe["meta"].update(
        {
            "generated_at": str(date.today()),
            "schema_version": "2.0",
            "total_unique_tickers": len(tickers),
            "us_listed": us,
            "tsx_listed": tsx,
            "tsx_venture_listed": tsxv,
            "no_data_tickers": no_data,
        }
    )

    print(f"Existing tickers migrated to v2 schema: {len(tickers) - len(added)}")
    print(f"New tickers added:                       {len(added)}")
    print(f"Existing tickers backfilled (new fields): {len(backfilled)}")
    print(f"Total tickers:                           {len(tickers)}")
    print(f"  US: {us}  TSX: {tsx}  TSX-V: {tsxv}")
    print(f"  no_data flags: {no_data}")

    if args.write:
        if args.backup:
            backup = UNIVERSE_PATH.with_suffix(".json.bak")
            shutil.copy(UNIVERSE_PATH, backup)
            print(f"\nBackup written to {backup}")
        with open(UNIVERSE_PATH, "w") as f:
            json.dump(universe, f, indent=2)
        print(f"Written to {UNIVERSE_PATH}")
    else:
        print("\nDry run — pass --write to apply changes.")


if __name__ == "__main__":
    main()
