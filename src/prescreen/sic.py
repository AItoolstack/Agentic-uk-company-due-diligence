"""
Plain-English labels for SIC 2007 codes an underwriter commonly meets.

Not the full list (that's ~730 codes): unknown codes show as the code alone,
with a link to Companies House's SIC lookup.
"""

from __future__ import annotations

SIC_LOOKUP_URL = "https://resources.companieshouse.gov.uk/sic/"

SIC = {
    "01110": "Growing of cereals and other crops",
    "10710": "Manufacture of bread, fresh pastry and cakes",
    "10890": "Manufacture of other food products",
    "16230": "Manufacture of builders' carpentry and joinery",
    "25620": "Machining",
    "33120": "Repair of machinery",
    "41100": "Development of building projects",
    "41201": "Construction of commercial buildings",
    "41202": "Construction of domestic buildings",
    "42990": "Construction of other civil engineering projects",
    "43210": "Electrical installation",
    "43220": "Plumbing, heat and air-conditioning installation",
    "43290": "Other construction installation",
    "43320": "Joinery installation",
    "43390": "Other building completion and finishing",
    "43999": "Other specialised construction activities",
    "45111": "Sale of new cars and light motor vehicles",
    "45200": "Maintenance and repair of motor vehicles",
    "46900": "Non-specialised wholesale trade",
    "47110": "Retail sale in non-specialised stores (mainly food)",
    "47190": "Other retail sale in non-specialised stores",
    "47910": "Retail sale via mail order or internet",
    "49410": "Freight transport by road",
    "52290": "Other transportation support activities",
    "55100": "Hotels and similar accommodation",
    "56101": "Licensed restaurants",
    "56102": "Unlicensed restaurants and cafes",
    "56302": "Public houses and bars",
    "62012": "Business and domestic software development",
    "62020": "Information technology consultancy",
    "62090": "Other information technology service activities",
    "63110": "Data processing, hosting and related activities",
    "64191": "Banks",
    "64205": "Activities of financial services holding companies",
    "64209": "Activities of other holding companies",
    "64999": "Financial intermediation not elsewhere classified",
    "65120": "Non-life insurance",
    "66190": "Activities auxiliary to financial intermediation",
    "66220": "Activities of insurance agents and brokers",
    "68100": "Buying and selling of own real estate",
    "68209": "Other letting and operating of own or leased real estate",
    "68320": "Management of real estate on a fee or contract basis",
    "69102": "Solicitors",
    "69201": "Accounting and auditing activities",
    "69202": "Bookkeeping activities",
    "70100": "Activities of head offices",
    "70229": "Management consultancy (other than financial management)",
    "71111": "Architectural activities",
    "71129": "Other engineering activities",
    "73110": "Advertising agencies",
    "74990": "Non-trading company",
    "77110": "Renting and leasing of cars and light motor vehicles",
    "78109": "Other activities of employment placement agencies",
    "81210": "General cleaning of buildings",
    "82990": "Other business support service activities",
    "85590": "Other education",
    "86900": "Other human health activities",
    "87100": "Residential nursing care facilities",
    "88100": "Social work without accommodation for the elderly and disabled",
    "93130": "Fitness facilities",
    "96020": "Hairdressing and other beauty treatment",
    "96090": "Other service activities",
    "99999": "Dormant company",
}


def label(code: str) -> str:
    return SIC.get(code, "")
