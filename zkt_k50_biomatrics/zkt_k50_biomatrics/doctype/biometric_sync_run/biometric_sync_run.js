frappe.ui.form.on('Biometric Sync Run', {refresh(frm) {
    if (['Queued', 'Running'].includes(frm.doc.status) && !frm._zkt_poll) {
        frm._zkt_poll = setInterval(() => {
            const route = frappe.get_route();
            if (route[0] !== 'Form' || route[2] !== frm.doc.name || !['Queued','Running'].includes(frm.doc.status)) {
                clearInterval(frm._zkt_poll); frm._zkt_poll = null; return;
            }
            frm.reload_doc();
        }, 5000);
    }
    frm.add_custom_button(__('Refresh Progress'), () => frm.reload_doc());
    frm.add_custom_button(__('Related Events'), () => frappe.set_route('List', 'Biometric Raw Log', {sync_run: frm.doc.name}));
    if (frm.doc.mode === 'Dry Run' && frm.doc.preview) {
        const preview = JSON.parse(frm.doc.preview);
        if (preview.counts) frm.dashboard.set_headline_alert(Object.entries(preview.counts).map(([k,v]) => `${frappe.utils.escape_html(k)}: ${Number(v)}`).join(' · '));
    }
}});
