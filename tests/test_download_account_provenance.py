"""The provenance of a ticket page that names no file (download_account_pages._TicketFlow._answers_this_click).

No browser: a fake one hands the flow its pages, their openers and frames, and the run's navigation record
(``NavigationStep``) as the session browser keeps it. The release-forms fixture tests drive the same check on
real headless Edge, where BiliFlow's own hop pages make most broken chains hard to build; here each case keeps
the good chain and changes exactly one thing, so every condition of the check has a case that only it decides.
Made-up ``.example`` URLs only.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from biliflow.download_account_browser import MAX_HOPS, NavigationStep
from biliflow.download_account_pages import _TicketFlow
from tests.test_download_account_browser import ALPHA, PORTAL, TICKETS

FILM = f"https://{PORTAL}/phim/501"
ACCESS = f"https://{PORTAL}/download-links/7801/access"
TICKET = f"https://{TICKETS}/x/01aa"
OTHER = f"https://{TICKETS}/x/02bb"


class FakePage:
    def __init__(self, url: str, opener: FakePage | None = None):
        self.url, self.opened_by, self.frame = url, opener, object()


class FakeBrowser:
    """What ``_TicketFlow`` asks the session browser for, with a navigation record the test writes."""

    def __init__(self):
        self.film = FakePage(FILM)
        self._pages = [self.film]
        self.steps: list[NavigationStep] = []
        self.navigation_number = 0
        self.navigations_dropped = False

    def pages(self):
        return list(self._pages)

    @staticmethod
    def page_url(page):
        return page.url

    @staticmethod
    def main_frame(page):
        return page.frame

    @staticmethod
    def opener(page):
        return page.opened_by

    def navigations(self, after: int = 0):
        return tuple(step for step in self.steps if step.number > after)

    def open(self, url: str, opener: FakePage) -> FakePage:
        page = FakePage(url, opener)
        self._pages.append(page)
        return page

    def step(self, url: str, frame, *, method: str = "GET", target: str | None = None, status: int | None = None,
             refused: str | None = None) -> None:
        self.navigation_number += 1
        self.steps.append(NavigationStep(self.navigation_number, method, url, frame, target, status, refused))


def flow_of(browser: FakeBrowser, request: str | None = ACCESS, method: str | None = "POST") -> _TicketFlow:
    """The flow of a click on a release-forms entry: its action is ``request``, sent with ``method`` (a POST form)."""
    return _TicketFlow(browser, SimpleNamespace(shows_ticket_ids=False), ALPHA, browser.film, "film:501", "7801",
                       until=0.0, request=request, request_method=method)


class ProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.browser = FakeBrowser()
        self.browser.step(FILM, self.browser.film.frame, status=200)  # before the click: never the click's
        self.flow = flow_of(self.browser, f"{ACCESS}#x")

    def popup_chain(self, *, opener: FakePage | None = None) -> FakePage:
        """The good chain: the click's POST (a popup's first request has no frame yet) redirects to the ticket,
        which the popup's frame loads next and shows."""
        popup = self.browser.open(TICKET, opener or self.browser.film)
        self.browser.step(ACCESS, None, method="POST", target=TICKET)
        self.browser.step(TICKET, popup.frame, status=200)
        return popup

    def test_the_clicks_own_request_and_its_checked_redirect_to_the_shown_ticket(self):
        self.assertTrue(self.flow._answers_this_click(self.popup_chain()))
        same_tab = FakeBrowser()  # a form without a target: the film page's own frame
        flow = flow_of(same_tab)
        same_tab.step(ACCESS, same_tab.film.frame, method="POST", target=f"{TICKET}#top")
        same_tab.step(TICKET, same_tab.film.frame, status=200)
        same_tab.film.url = TICKET
        self.assertTrue(flow._answers_this_click(same_tab.film))

    def test_a_get_of_the_forms_action_is_not_the_selected_forms_post(self):
        # The good chain but for its first request: a GET of the same action (a link or a script, not the form).
        popup = self.browser.open(TICKET, self.browser.film)
        self.browser.step(ACCESS, None, method="GET", target=TICKET)
        self.browser.step(TICKET, popup.frame, status=200)
        self.assertFalse(self.flow._answers_this_click(popup))
        same_tab = FakeBrowser()
        flow = flow_of(same_tab)
        same_tab.step(ACCESS, same_tab.film.frame, method="GET", target=TICKET)
        same_tab.step(TICKET, same_tab.film.frame, status=200)
        same_tab.film.url = TICKET
        self.assertFalse(flow._answers_this_click(same_tab.film))

    def test_the_first_request_matches_the_entrys_own_method_and_the_redirects_after_it_are_gets(self):
        # The good chains above already follow the POST with a GET of its redirect target. An entry whose action
        # is a GET (a link) takes only a GET; an entry without its method takes nothing.
        for method, sent, taken in (("GET", "GET", True), ("GET", "POST", False), (None, "POST", False)):
            with self.subTest(method=method, sent=sent):
                browser = FakeBrowser()
                flow = flow_of(browser, method=method)
                popup = browser.open(TICKET, browser.film)
                browser.step(ACCESS, None, method=sent, target=TICKET)
                browser.step(TICKET, popup.frame, status=200)
                self.assertEqual(flow._answers_this_click(popup), taken)

    def test_a_ticket_page_opened_by_another_page_than_the_film_page(self):
        relay = self.browser.open(f"https://{PORTAL}/relay", self.browser.film)
        self.assertFalse(self.flow._answers_this_click(self.popup_chain(opener=relay)))

    def test_two_requests_to_the_entrys_url_since_the_click(self):
        popup = self.popup_chain()
        second = self.browser.open(OTHER, self.browser.film)  # a second submit: two tickets, neither is taken
        self.browser.step(ACCESS, None, method="POST", target=OTHER)
        self.browser.step(OTHER, second.frame, status=200)
        self.assertFalse(self.flow._answers_this_click(popup))
        self.assertFalse(self.flow._answers_this_click(second))

    def test_a_frame_whose_next_navigation_is_not_the_redirects_target(self):
        popup = self.browser.open(OTHER, self.browser.film)
        self.browser.step(ACCESS, None, method="POST", target=TICKET)
        self.browser.step(OTHER, popup.frame, status=200)  # another ticket came first: the redirect was not followed
        self.assertFalse(self.flow._answers_this_click(popup))

    def test_a_redirect_never_followed(self):
        popup = self.browser.open(TICKET, self.browser.film)
        self.browser.step(ACCESS, None, method="POST", target=TICKET)
        self.assertFalse(self.flow._answers_this_click(popup))

    def test_a_ticket_page_that_went_elsewhere_and_came_back(self):
        popup = self.popup_chain()
        self.browser.step(OTHER, popup.frame, status=200)
        self.browser.step(TICKET, popup.frame, status=200)
        self.assertFalse(self.flow._answers_this_click(popup))

    def test_a_refused_or_failed_last_step_or_another_shown_url(self):
        for status, refused in ((None, "HOST_NOT_ALLOWED"), (404, None), (302, None)):
            with self.subTest(status=status, refused=refused):
                self.setUp()
                popup = self.browser.open(TICKET, self.browser.film)
                self.browser.step(ACCESS, None, method="POST", target=TICKET)
                self.browser.step(TICKET, popup.frame, status=status, refused=refused)
                self.assertFalse(self.flow._answers_this_click(popup))
        self.setUp()
        popup = self.popup_chain()
        popup.url = OTHER  # the page shows something else than the chain's end
        self.assertFalse(self.flow._answers_this_click(popup))

    def test_a_page_from_before_the_click_another_frame_a_lost_record_or_no_request(self):
        before = self.browser.open(TICKET, self.browser.film)
        flow = flow_of(self.browser)
        self.browser.step(ACCESS, None, method="POST", target=TICKET)
        self.browser.step(TICKET, before.frame, status=200)
        self.assertFalse(flow._answers_this_click(before))  # it was open before this click

        self.setUp()
        popup = self.browser.open(TICKET, self.browser.film)
        self.browser.step(ACCESS, object(), method="POST", target=TICKET)  # asked for by another frame
        self.browser.step(TICKET, popup.frame, status=200)
        self.assertFalse(self.flow._answers_this_click(popup))

        self.setUp()
        popup = self.popup_chain()
        self.browser.navigations_dropped = True
        self.assertFalse(self.flow._answers_this_click(popup))

        self.setUp()
        self.assertFalse(flow_of(self.browser, None)._answers_this_click(self.popup_chain()))

    def test_a_redirect_chain_longer_than_the_browsers_hops(self):
        popup = self.browser.open(TICKET, self.browser.film)
        hops = [f"https://{TICKETS}/hop/{index}" for index in range(MAX_HOPS + 1)]
        self.browser.step(ACCESS, None, method="POST", target=hops[0])
        for index, url in enumerate(hops):
            self.browser.step(url, popup.frame, target=hops[index + 1] if index + 1 < len(hops) else TICKET)
        self.browser.step(TICKET, popup.frame, status=200)
        self.assertFalse(self.flow._answers_this_click(popup))


if __name__ == "__main__":
    unittest.main()
