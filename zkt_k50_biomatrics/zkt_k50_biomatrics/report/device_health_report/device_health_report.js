frappe.query_reports["Device Health Report"] = {filters: [
    {fieldname:'from_date', label:__('From Date'), fieldtype:'Date'},
    {fieldname:'to_date', label:__('To Date'), fieldtype:'Date'},
    {fieldname:'device', label:__('Device'), fieldtype:'Link', options:'Biometric Device'},
    {fieldname:'agent', label:__('Agent'), fieldtype:'Link', options:'Biometric Agent'},
    {fieldname:'employee', label:__('Employee'), fieldtype:'Link', options:'Employee'},
    {fieldname:'status', label:__('Status'), fieldtype:'Data'},
    {fieldname:'sync_run', label:__('Sync Run'), fieldtype:'Link', options:'Biometric Sync Run'}
]};
