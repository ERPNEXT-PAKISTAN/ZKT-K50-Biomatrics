frappe.ui.form.on('Biometric Agent', { refresh(frm) {
    if (frm.is_new() || !['System Manager', 'Biometric Administrator'].some(r => frappe.user.has_role(r))) return;
    frm.add_custom_button(__('Enroll / Rotate Credentials'), () => frappe.confirm(
        __('Generate a 15-minute one-time enrollment code? Enrolling replaces the previous agent credential.'), () =>
        frappe.call({method: 'zkt_k50_biomatrics.api.create_enrollment', args: {agent: frm.doc.name}, callback: r =>
            frappe.msgprint({title: __('One-Time Enrollment'), message: '<p>' + __('Enter this once in the agent enrollment prompt:') + '</p><pre>' + frappe.utils.escape_html(r.message.credential) + '</pre>'})})));
    frm.add_custom_button(__('Revoke Agent'), () => frappe.confirm(__('Immediately revoke this agent?'), () =>
        frappe.call({method: 'zkt_k50_biomatrics.api.revoke_agent', args: {agent: frm.doc.name}, callback: () => frm.reload_doc()})));
}});
