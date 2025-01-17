"""Objects with site methods independent of the communication interface."""
#
# (C) Pywikibot team, 2008-2021
#
# Distributed under the terms of the MIT license.
#
import functools
import re
import threading

from warnings import warn

import pywikibot

from pywikibot.exceptions import Error, FamilyMaintenanceWarning, UnknownSite
from pywikibot.site._namespace import Namespace, NamespacesDict
from pywikibot.throttle import Throttle
from pywikibot.tools import (
    ComparableMixin,
    deprecated,
    first_upper,
    normalize_username,
    remove_last_args,
    SelfCallString,
)


class PageInUse(Error):

    """Page cannot be reserved for writing due to existing lock."""


class BaseSite(ComparableMixin):

    """Site methods that are independent of the communication interface."""

    @remove_last_args(['sysop'])
    def __init__(self, code: str, fam=None, user=None) -> None:
        """
        Initializer.

        @param code: the site's language code
        @type code: str
        @param fam: wiki family name (optional)
        @type fam: str or pywikibot.family.Family
        @param user: bot user name (optional)
        @type user: str
        """
        if code.lower() != code:
            # Note the Site function in __init__ also emits a UserWarning
            # for this condition, showing the callers file and line no.
            pywikibot.log('BaseSite: code "{}" converted to lowercase'
                          .format(code))
            code = code.lower()
        if not all(x in pywikibot.family.CODE_CHARACTERS for x in code):
            pywikibot.log('BaseSite: code "{}" contains invalid characters'
                          .format(code))
        self.__code = code
        if isinstance(fam, str) or fam is None:
            self.__family = pywikibot.family.Family.load(fam)
        else:
            self.__family = fam

        self.obsolete = False
        # if we got an outdated language code, use the new one instead.
        if self.__code in self.__family.obsolete:
            if self.__family.obsolete[self.__code] is not None:
                self.__code = self.__family.obsolete[self.__code]
                # Note the Site function in __init__ emits a UserWarning
                # for this condition, showing the callers file and line no.
                pywikibot.log('Site {} instantiated using aliases code of {}'
                              .format(self, code))
            else:
                # no such language anymore
                self.obsolete = True
                pywikibot.log('Site %s instantiated and marked "obsolete" '
                              'to prevent access' % self)
        elif self.__code not in self.languages():
            if self.__family.name in self.__family.langs \
               and len(self.__family.langs) == 1:
                self.__code = self.__family.name
                if self.__family == pywikibot.config.family \
                   and code == pywikibot.config.mylang:
                    pywikibot.config.mylang = self.__code
                    warn('Global configuration variable "mylang" changed to '
                         '"%s" while instantiating site %s'
                         % (self.__code, self), UserWarning)
            else:
                raise UnknownSite("Language '%s' does not exist in family %s"
                                  % (self.__code, self.__family.name))

        self._username = normalize_username(user)

        self.use_hard_category_redirects = (
            self.code in self.family.use_hard_category_redirects)

        # following are for use with lock_page and unlock_page methods
        self._pagemutex = threading.Condition()
        self._locked_pages = set()

    @property
    def throttle(self):
        """Return this Site's throttle. Initialize a new one if needed."""
        if not hasattr(self, '_throttle'):
            self._throttle = Throttle(self, multiplydelay=True)
        return self._throttle

    @property
    def family(self):
        """The Family object for this Site's wiki family."""
        return self.__family

    @property
    def code(self):
        """
        The identifying code for this Site equal to the wiki prefix.

        By convention, this is usually an ISO language code, but it does
        not have to be.
        """
        return self.__code

    @property
    def lang(self):
        """The ISO language code for this Site.

        Presumed to be equal to the site code, but this can be overridden.
        """
        return self.__code

    @property
    def doc_subpage(self):
        """
        Return the documentation subpage for this Site.

        @rtype: tuple
        """
        if not hasattr(self, '_doc_subpage'):
            try:
                doc, codes = self.family.doc_subpages.get('_default', ((), []))
                if self.code not in codes:
                    try:
                        doc = self.family.doc_subpages[self.code]
                    # Language not defined in doc_subpages in x_family.py file
                    # It will use default for the family.
                    # should it just raise an Exception and fail?
                    # this will help to check the dictionary ...
                    except KeyError:
                        warn('Site {0} has no language defined in '
                             'doc_subpages dict in {1}_family.py file'
                             .format(self, self.family.name),
                             FamilyMaintenanceWarning, 2)
            # doc_subpages not defined in x_family.py file
            except AttributeError:
                doc = ()  # default
                warn('Site {0} has no doc_subpages dict in {1}_family.py file'
                     .format(self, self.family.name),
                     FamilyMaintenanceWarning, 2)
            self._doc_subpage = doc

        return self._doc_subpage

    def _cmpkey(self):
        """Perform equality and inequality tests on Site objects."""
        return (self.family.name, self.code)

    def __getstate__(self):
        """Remove Lock based classes before pickling."""
        new = self.__dict__.copy()
        del new['_pagemutex']
        if '_throttle' in new:
            del new['_throttle']
        # site cache contains exception information, which can't be pickled
        if '_iw_sites' in new:
            del new['_iw_sites']
        return new

    def __setstate__(self, attrs):
        """Restore things removed in __getstate__."""
        self.__dict__.update(attrs)
        self._pagemutex = threading.Condition()

    def user(self):
        """Return the currently-logged in bot username, or None."""
        if self.logged_in():
            return self.username()
        else:
            return None

    @remove_last_args(['sysop'])
    def username(self):
        """Return the username used for the site."""
        return self._username

    def __getattr__(self, attr):
        """Delegate undefined methods calls to the Family object."""
        if hasattr(self.__class__, attr):
            return getattr(self.__class__, attr)
        try:
            method = getattr(self.family, attr)
            if not callable(method):
                raise AttributeError
            f = functools.partial(method, self.code)
            if hasattr(method, '__doc__'):
                f.__doc__ = method.__doc__
            return f
        except AttributeError:
            raise AttributeError("%s instance has no attribute '%s'"
                                 % (self.__class__.__name__, attr))

    def __str__(self):
        """Return string representing this Site's name and code."""
        return self.family.name + ':' + self.code

    @property
    def sitename(self):
        """String representing this Site's name and code."""
        return SelfCallString(self.__str__())

    def __repr__(self):
        """Return internal representation."""
        return '{0}("{1}", "{2}")'.format(
            self.__class__.__name__, self.code, self.family)

    def __hash__(self):
        """Return hashable key."""
        return hash(repr(self))

    def languages(self):
        """Return list of all valid language codes for this site's Family."""
        return list(self.family.langs.keys())

    def validLanguageLinks(self):  # noqa: N802
        """Return list of language codes to be used in interwiki links."""
        return [lang for lang in self.languages()
                if self.namespaces.lookup_normalized_name(lang) is None]

    def _interwiki_urls(self, only_article_suffixes=False):
        base_path = self.path()
        if not only_article_suffixes:
            yield base_path
        yield base_path + '/'
        yield base_path + '?title='
        yield self.article_path

    @deprecated('APISite.namespaces.lookup_name', since='20150703',
                future_warning=True)
    def ns_index(self, namespace):  # pragma: no cover
        """
        Return the Namespace for a given namespace name.

        @param namespace: name
        @type namespace: str
        @return: The matching Namespace object on this Site
        @rtype: Namespace, or None if invalid
        """
        return self.namespaces.lookup_name(namespace)

    @deprecated('APISite.namespaces.lookup_name', since='20150703',
                future_warning=True)  # noqa: N802
    def getNamespaceIndex(self, namespace):
        """DEPRECATED: Return the Namespace for a given namespace name."""
        return self.namespaces.lookup_name(namespace)

    def _build_namespaces(self):
        """Create default namespaces."""
        return Namespace.builtin_namespaces()

    @property
    def namespaces(self):
        """Return dict of valid namespaces on this wiki."""
        if not hasattr(self, '_namespaces'):
            self._namespaces = NamespacesDict(self._build_namespaces())
        return self._namespaces

    def ns_normalize(self, value):
        """
        Return canonical local form of namespace name.

        @param value: A namespace name
        @type value: str

        """
        index = self.namespaces.lookup_name(value)
        return self.namespace(index)

    @remove_last_args(('default', ))
    def redirect(self):
        """Return list of localized redirect tags for the site."""
        return ['REDIRECT']

    @remove_last_args(('default', ))
    def pagenamecodes(self):
        """Return list of localized PAGENAME tags for the site."""
        return ['PAGENAME']

    @remove_last_args(('default', ))
    def pagename2codes(self):
        """Return list of localized PAGENAMEE tags for the site."""
        return ['PAGENAMEE']

    def lock_page(self, page, block=True):
        """
        Lock page for writing. Must be called before writing any page.

        We don't want different threads trying to write to the same page
        at the same time, even to different sections.

        @param page: the page to be locked
        @type page: pywikibot.Page
        @param block: if true, wait until the page is available to be locked;
            otherwise, raise an exception if page can't be locked

        """
        title = page.title(with_section=False)
        with self._pagemutex:
            while title in self._locked_pages:
                if not block:
                    raise PageInUse(title)
                self._pagemutex.wait()
            self._locked_pages.add(title)

    def unlock_page(self, page):
        """
        Unlock page. Call as soon as a write operation has completed.

        @param page: the page to be locked
        @type page: pywikibot.Page

        """
        with self._pagemutex:
            self._locked_pages.discard(page.title(with_section=False))
            self._pagemutex.notify_all()

    def disambcategory(self):
        """Return Category in which disambig pages are listed."""
        if self.has_data_repository:
            repo = self.data_repository()
            repo_name = repo.family.name
            try:
                item = self.family.disambcatname[repo.code]
            except KeyError:
                raise Error(
                    'No {repo} qualifier found for disambiguation category '
                    'name in {fam}_family file'.format(repo=repo_name,
                                                       fam=self.family.name))

            dp = pywikibot.ItemPage(repo, item)
            try:
                name = dp.getSitelink(self)
            except pywikibot.NoPage:
                raise Error(
                    'No disambiguation category name found in {repo} '
                    'for {site}'.format(repo=repo_name, site=self))

        else:  # fallback for non WM sites
            try:
                name = '{}:{}'.format(Namespace.CATEGORY,
                                      self.family.disambcatname[self.code])
            except KeyError:
                raise Error(
                    'No disambiguation category name found in '
                    '{site.family.name}_family for {site}'.format(site=self))

        return pywikibot.Category(pywikibot.Link(name, self))

    def isInterwikiLink(self, text):  # noqa: N802
        """Return True if text is in the form of an interwiki link.

        If a link object constructed using "text" as the link text parses as
        belonging to a different site, this method returns True.

        """
        linkfam, linkcode = pywikibot.Link(text, self).parse_site()
        return linkfam != self.family.name or linkcode != self.code

    def redirectRegex(self, pattern=None):  # noqa: N802
        """Return a compiled regular expression matching on redirect pages.

        Group 1 in the regex match object will be the target title.

        """
        if pattern is None:
            pattern = 'REDIRECT'
        # A redirect starts with hash (#), followed by a keyword, then
        # arbitrary stuff, then a wikilink. The wikilink may contain
        # a label, although this is not useful.
        return re.compile(r'\s*#{pattern}\s*:?\s*\[\[(.+?)(?:\|.*?)?\]\]'
                          .format(pattern=pattern), re.IGNORECASE | re.DOTALL)

    def sametitle(self, title1: str, title2: str) -> bool:
        """
        Return True if title1 and title2 identify the same wiki page.

        title1 and title2 may be unequal but still identify the same page,
        if they use different aliases for the same namespace.
        """
        def ns_split(title):
            """Separate the namespace from the name."""
            ns, delim, name = title.partition(':')
            if delim:
                ns = self.namespaces.lookup_name(ns)
            if not delim or not ns:
                return default_ns, title
            else:
                return ns, name

        # Replace underscores with spaces and multiple combinations of them
        # with only one space
        title1 = re.sub(r'[_ ]+', ' ', title1)
        title2 = re.sub(r'[_ ]+', ' ', title2)
        if title1 == title2:
            return True

        default_ns = self.namespaces[0]
        # determine whether titles contain namespace prefixes
        ns1_obj, name1 = ns_split(title1)
        ns2_obj, name2 = ns_split(title2)
        if ns1_obj != ns2_obj:
            # pages in different namespaces
            return False

        name1 = name1.strip()
        name2 = name2.strip()
        # If the namespace has a case definition it's overriding the site's
        # case definition
        if ns1_obj.case == 'first-letter':
            name1 = first_upper(name1)
            name2 = first_upper(name2)
        return name1 == name2

    # namespace shortcuts for backwards-compatibility

    @deprecated('namespaces.SPECIAL.custom_name', since='20160407')
    def special_namespace(self):
        """Return local name for the Special: namespace."""
        return self.namespace(-1)

    @deprecated('namespaces.FILE.custom_name', since='20160407')
    def image_namespace(self):
        """Return local name for the File namespace."""
        return self.namespace(6)

    @deprecated('namespaces.MEDIAWIKI.custom_name', since='20160407')
    def mediawiki_namespace(self):
        """Return local name for the MediaWiki namespace."""
        return self.namespace(8)

    @deprecated('namespaces.TEMPLATE.custom_name', since='20160407')
    def template_namespace(self):
        """Return local name for the Template namespace."""
        return self.namespace(10)

    @deprecated('namespaces.CATEGORY.custom_name', since='20160407')
    def category_namespace(self):
        """Return local name for the Category namespace."""
        return self.namespace(14)

    @deprecated('list(namespaces.CATEGORY)', since='20150829',
                future_warning=True)
    def category_namespaces(self):  # pragma: no cover
        """Return names for the Category namespace."""
        return list(self.namespace(14, all=True))

    # site-specific formatting preferences

    def category_on_one_line(self):
        # TODO: is this even needed? No family in the framework uses it.
        """Return True if this site wants all category links on one line."""
        return self.code in self.family.category_on_one_line

    def interwiki_putfirst(self):
        """Return list of language codes for ordering of interwiki links."""
        return self.family.interwiki_putfirst.get(self.code, None)

    def getSite(self, code):  # noqa: N802
        """Return Site object for language 'code' in this Family."""
        return pywikibot.Site(code=code, fam=self.family, user=self.user())
