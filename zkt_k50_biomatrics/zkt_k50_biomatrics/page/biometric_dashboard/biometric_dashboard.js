frappe.pages['biometric-dashboard'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: __('ZKT K50 Biomatrics'), single_column: true});
    const area = $('<div class="p-4"></div>').appendTo(page.main);
    const load = () => frappe.call({method: 'zkt_k50_biomatrics.api.dashboard', callback: r => {
        area.empty();
        $('<p class="text-muted"></p>').text(__('Devices seen and agents online use a 15-minute window. Receipt means durable storage; review raw logs for processing outcomes.')).appendTo(area);
        const cards = $('<div class="row"></div>').appendTo(area);
        Object.entries(r.message).forEach(([label, value]) => {
            const card = $('<div class="col-md-3 mb-4"><div class="frappe-card p-4"><div class="text-muted"></div><h2></h2></div></div>').appendTo(cards);
            card.find('.text-muted').text(__(label)); card.find('h2').text(value);
        });
        const actions = $('<div class="d-flex flex-wrap" style="gap:12px"></div>').appendTo(area);
        ['Biometric Device','Biometric Agent','Biometric Employee Mapping','Biometric Raw Log','Biometric Sync Run'].forEach(dt => {
            $('<button class="btn btn-default"></button>').text(__(dt)).on('click', () => frappe.set_route('List', dt)).appendTo(actions);
        });
        const reports = $('<div class="mt-4"></div>').appendTo(area);
        $('<h4></h4>').text(__('Reports')).appendTo(reports);
        ['Device Health Report','Synchronization Run Report','Attendance Integration Error Report','Unmapped Biometric Users Report','Raw Attendance Event Report','Duplicate Events Report','Daily Device Event Summary','Agent Status Report'].forEach(name => {
            $('<button class="btn btn-link"></button>').text(__(name)).on('click', () => frappe.set_route('query-report',name)).appendTo(reports);
        });
    }});
    page.set_primary_action(__('Refresh'), load); load();
};
