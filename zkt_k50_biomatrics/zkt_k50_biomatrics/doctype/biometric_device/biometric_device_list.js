frappe.listview_settings['Biometric Device'] = {
    add_fields: ['enabled', 'last_status', 'last_sync'],
    get_indicator(doc) {
        if (!doc.enabled) return [__('Disabled'), 'grey', 'enabled,=,0'];
        const color = {Success:'green', Partial:'orange', Failed:'red', Running:'blue', Queued:'blue'}[doc.last_status] || 'grey';
        return [__(doc.last_status || 'Never Synced'), color, 'last_status,=,' + doc.last_status];
    }
};
