frappe.ui.form.on('Biometric Device', {
    refresh(frm) {
        if (frm.is_new()) return;
        const manager = ['System Manager', 'Biometric Administrator', 'Biometric Manager'].some(r => frappe.user.has_role(r));
        frm.add_custom_button(__('View Logs'), () => frappe.set_route('List', 'Biometric Raw Log', {device: frm.doc.name}));
        frm.add_custom_button(__('View Mappings'), () => frappe.set_route('List', 'Biometric Employee Mapping', {device: frm.doc.name}));
        frm.add_custom_button(__('Sync Runs'), () => frappe.set_route('List', 'Biometric Sync Run', {device: frm.doc.name}));
        if (!manager) return;
        for (const operation of ['Test Connection', 'Device Info', 'Fetch Users']) {
            frm.add_custom_button(__(operation), () => start(frm, operation, true), __('Device'));
        }
        frm.add_custom_button(__('Preview Attendance'), () => start(frm, 'Sync', true), __('Synchronize'));
        frm.add_custom_button(__('Start Sync'), () => frappe.confirm(
            __('Create Employee Checkins from this device? Review mappings, location and preview first. Site: {0}', [frappe.boot.sitename]),
            () => start(frm, 'Sync', false)), __('Synchronize'));
        frm.add_custom_button(__('Historical Import'), () => {
            const dialog = new frappe.ui.Dialog({title: __('Historical Import'), fields: [
                {fieldtype: 'HTML', options: `<p>${frappe.utils.escape_html(__('Device: {0}. Site: {1}. Maximum 31 days. Preview before live import.', [frm.doc.name, frappe.boot.sitename]))}</p>`},
                {fieldname: 'start', label: __('Start'), fieldtype: 'Datetime', reqd: 1},
                {fieldname: 'end', label: __('End'), fieldtype: 'Datetime', reqd: 1},
                {fieldname: 'dry_run', label: __('Dry Run'), fieldtype: 'Check', default: 1}
            ], primary_action_label: __('Queue Import'), primary_action(values) {
                const execute = () => { start(frm, 'Sync', !!values.dry_run, values.start, values.end); dialog.hide(); };
                if (values.dry_run) execute();
                else frappe.confirm(__('This historical import can create many checkins. Continue?'), execute);
            }});
            dialog.show();
        }, __('Synchronize'));
        frm.add_custom_button(__('Retry Failed Events'), () => frappe.call({
            method: 'zkt_k50_biomatrics.api.retry_events', args: {device: frm.doc.name},
            callback: r => frappe.msgprint(__('Queued {0} events', [r.message.queued]))
        }), __('Synchronize'));
        frm.add_custom_button(__('Disable'), async () => {
            await frm.set_value('enabled', 0); await frm.set_value('live_sync_enabled', 0); await frm.save();
        }, __('Synchronize'));
        frm.add_custom_button(__('Map Discovered Users'), () => show_users(frm), __('Device'));
        frm.dashboard.set_headline_alert(__('Only verified pyzk Pull-compatible devices are supported. Device memory is never cleared.'));
    }
});
function start(frm, operation, dry_run, start, end) {
    if (frm.is_dirty()) { frappe.msgprint(__('Save the device settings first.')); return; }
    frappe.call({method: 'zkt_k50_biomatrics.api.start_operation',
        args: {device: frm.doc.name, operation, dry_run: dry_run ? 1 : 0, start, end},
        callback: r => frappe.set_route('Form', 'Biometric Sync Run', r.message)});
}
async function show_users(frm) {
    const response = await frappe.call({method: 'zkt_k50_biomatrics.api.discovered_users', args: {device: frm.doc.name}});
    const users = response.message;
    if (!users.length) { frappe.msgprint(__('Run Fetch Users, then reload the device.')); return; }
    const dialog = new frappe.ui.Dialog({title: __('Map Discovered Users'), size: 'extra-large', fields: [
        {fieldtype: 'HTML', options: '<p>' + __('Select Employees explicitly. Existing mappings will not be overwritten. Review existing mappings using View Mappings.') + '</p>'},
        {fieldname: 'effective_from', label: __('Effective From'), fieldtype: 'Datetime', reqd: 1, default: frm.doc.sync_start},
        {fieldname: 'users', fieldtype: 'Table', label: __('Users'), cannot_add_rows: true, data: users.slice(0, 200), fields: [
            {fieldname: 'user_id', label: __('Biometric ID'), fieldtype: 'Data', read_only: 1, in_list_view: 1},
            {fieldname: 'device_user_name', label: __('Device Name'), fieldtype: 'Data', read_only: 1, in_list_view: 1},
            {fieldname: 'current_employee', label: __('Current Employee'), fieldtype: 'Data', read_only: 1, in_list_view: 1},
            {fieldname: 'employee', label: __('Employee'), fieldtype: 'Link', options: 'Employee', in_list_view: 1}
        ]}
    ], primary_action_label: __('Create Mappings'), primary_action(values) {
        const mappings = values.users.filter(u => u.employee).map(u => ({user_id: u.user_id, employee: u.employee, effective_from: values.effective_from}));
        if (!mappings.length) return;
        frappe.call({method: 'zkt_k50_biomatrics.api.map_users', args: {device: frm.doc.name, mappings},
            callback: () => {dialog.hide(); frappe.show_alert(__('Mappings created'));}});
    }});
    dialog.show();
}
