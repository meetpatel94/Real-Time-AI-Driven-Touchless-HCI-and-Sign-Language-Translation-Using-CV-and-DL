/**
 * Navigation & responsive sidebar handler.
 *
 * Desktop keeps the original collapsible rail. On phones the same sidebar is
 * presented as a temporary drawer so all existing links remain available
 * without permanently taking content width.
 */
document.addEventListener('DOMContentLoaded', () => {
    const sidebar = document.getElementById('sidebar');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const mobileToggle = document.getElementById('mobile-nav-toggle');
    const mobileScrim = document.getElementById('mobile-nav-scrim');
    const currentPath = window.location.pathname;
    const mobileQuery = window.matchMedia('(max-width: 767px)');

    const isMobile = () => mobileQuery.matches;

    const setMobileNavigation = (open) => {
        if (!sidebar) return;
        sidebar.classList.toggle('mobile-open', open);
        document.body.classList.toggle('mobile-nav-open', open);
        if (mobileToggle) {
            mobileToggle.setAttribute('aria-expanded', String(open));
            mobileToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
        }
        if (mobileScrim) {
            mobileScrim.setAttribute('aria-hidden', String(!open));
        }
    };

    // Restore the desktop rail state. The mobile drawer always starts closed,
    // even if the desktop state was collapsed on a previous visit.
    const isCollapsed = localStorage.getItem('sidebar_collapsed') === 'true';
    if (isCollapsed && sidebar) {
        sidebar.classList.add('collapsed');
    }

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', () => {
            if (isMobile()) {
                setMobileNavigation(!sidebar.classList.contains('mobile-open'));
                return;
            }
            sidebar.classList.toggle('collapsed');
            localStorage.setItem('sidebar_collapsed', sidebar.classList.contains('collapsed'));
        });
    }

    if (mobileToggle) {
        mobileToggle.addEventListener('click', () => {
            setMobileNavigation(!sidebar || !sidebar.classList.contains('mobile-open'));
        });
    }

    if (mobileScrim) {
        mobileScrim.addEventListener('click', () => setMobileNavigation(false));
    }

    // Set Active Link State and close the drawer after a mobile selection.
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        const link = item.querySelector('a');
        if (!link) return;
        if (link.getAttribute('href') === currentPath) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
        link.addEventListener('click', () => {
            if (isMobile()) setMobileNavigation(false);
        });
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && sidebar && sidebar.classList.contains('mobile-open')) {
            setMobileNavigation(false);
            mobileToggle?.focus();
        }
    });

    const handleViewportChange = event => {
        if (!event.matches) {
            setMobileNavigation(false);
        }
    };

    if (typeof mobileQuery.addEventListener === 'function') {
        mobileQuery.addEventListener('change', handleViewportChange);
    } else if (typeof mobileQuery.addListener === 'function') {
        mobileQuery.addListener(handleViewportChange);
    }
});
