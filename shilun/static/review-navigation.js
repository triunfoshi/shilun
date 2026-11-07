(() => {
    "use strict";
    for (const id of ["tab-review", "tab-sentiment"]) {
        const panel = document.getElementById(id);
        const nav = panel?.querySelector('.review-loop-nav');
        if (!nav) continue;
        const topbar = document.querySelector('.topbar');
        const links = [...nav.querySelectorAll('a[href^="#"]')];
        let scheduled = false;
        const visible = target => target && target.getClientRects().length > 0;
        function update() {
            scheduled = false;
            if (panel.hidden || !visible(panel)) return;
            const topbarPosition = topbar && getComputedStyle(topbar).position;
            const top = ['sticky','fixed'].includes(topbarPosition) ? topbar.getBoundingClientRect().height : 0;
            const height = nav.getBoundingClientRect().height;
            panel.style.setProperty('--review-nav-top', `${top}px`);
            panel.style.setProperty('--review-target-offset', `${top + height + 12}px`);
            let current = links[0];
            for (const link of links) {
                const target = document.getElementById(link.hash.slice(1));
                if (visible(target) && target.getBoundingClientRect().top <= top + height + 28) current = link;
            }
            if (window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 4) {
                current = links.filter(link => visible(document.getElementById(link.hash.slice(1)))).at(-1) || current;
            }
            for (const link of links) {
                if (link === current) link.setAttribute('aria-current', 'location');
                else link.removeAttribute('aria-current');
            }
        }
        function schedule() {
            if (!scheduled) { scheduled = true; requestAnimationFrame(update); }
        }
        for (const link of links) link.addEventListener('click', event => {
            if (link.dataset.sentimentPanel && typeof window.selectSentimentSection === 'function') {
                window.selectSentimentSection(link.dataset.sentimentPanel);
            }
            const target = document.getElementById(link.hash.slice(1));
            if (!visible(target)) return;
            event.preventDefault();
            update();
            target.scrollIntoView({block:'start', behavior:'instant'});
            schedule();
        });
        window.addEventListener('scroll', schedule, {passive:true});
        window.addEventListener('resize', schedule, {passive:true});
        if (typeof ResizeObserver === 'function') {
            const observer = new ResizeObserver(schedule);
            observer.observe(nav);
            if (topbar) observer.observe(topbar);
        }
        new MutationObserver(schedule).observe(panel, {attributes:true, attributeFilter:['hidden','class']});
        new MutationObserver(schedule).observe(panel, {childList:true, subtree:true});
        schedule();
    }
})();
