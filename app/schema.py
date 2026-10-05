SHIPMENT_COLUMNS = {
    "Date": "date",                                     
    "Voucher No.": "voucher_no",                         
    "Customer": "customer",                            
    "Transporter": "transporter",                        
    "State (matched)": "destination_state",              
    "Approx. Distance from Mumbai (km)": "distance_km",  
    "Region Class": "region_class",                      
}
SHIPMENT_REQUIRED = ["date", "voucher_no", "customer", "transporter"]

TRANSPORTER_COLUMNS = {
    "Transporter Name": "transporter_name",
    "Historical Shipments (Mumbai div.)": "historical_shipments",
    "Avg. Distance of Shipments (km, est.)": "avg_distance_km",
    "Suggested Type (data-derived, verify)": "suggested_type",
    "Type (Regional/National)": "confirmed_type",
    "Vehicle Capacity": "vehicle_capacity_tons",
    "Route Coverage": "route_coverage",
    "Phone": "phone",
    "Availability": "availability",
    "Acceptance Rate": "acceptance_rate",
}
TRANSPORTER_REQUIRED = ["transporter_name"]

OPS_SUPPLIED_FIELDS = [
    "confirmed_type",
    "vehicle_capacity_tons",
    "route_coverage",
    "phone",
    "availability",
    "acceptance_rate",
]

SHEET_SHIPMENTS = "Mumbai Shipments"
SHEET_TRANSPORTERS = "Transporter Master"
SHEET_CUSTOMERS = "Customer & Destination Master"

REGIONAL_DISTANCE_THRESHOLD_KM = 530
