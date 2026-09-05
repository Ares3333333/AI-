from html.parser import HTMLParser
import json
import re

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title=''; self.h1=[]; self.description=''; self.canonical=''; self.robots=[]
        self.links=[]; self.contact=False; self.terms=False; self.jsonld=[]
        self._title=False; self._h1=False; self._ld=False; self._text=''
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='title': self._title=True
        if tag=='h1': self._h1=True; self.h1.append('')
        if tag=='meta':
            if a.get('name','').lower()=='description': self.description=a.get('content','')[:2000]
            if a.get('name','').lower() in ('robots','googlebot','yandex'): self.robots.append(a.get('content','')[:1000])
        if tag=='link' and 'canonical' in a.get('rel','').lower().split(): self.canonical=a.get('href','')[:2048]
        if tag=='a':
            h=a.get('href','')[:2048]; low=h.lower()
            self.contact=self.contact or low.startswith('mailto:') or bool(re.search(r'(contact|kontakt|контакт)',low))
            self.terms=self.terms or bool(re.search(r'(privacy|terms|policy|oferta|услов|политик)',low))
            if not low.startswith(('mailto:','tel:','javascript:','data:')) and len(self.links)<500: self.links.append(h)
        if tag=='script' and a.get('type','').lower()=='application/ld+json': self._ld=True; self._text=''
    def handle_endtag(self,tag):
        if tag=='title': self._title=False
        if tag=='h1': self._h1=False
        if tag=='script' and self._ld: self.jsonld.append(self._text); self._ld=False
    def handle_data(self,data):
        if self._title: self.title=(self.title+data)[:2000]
        if self._h1 and self.h1: self.h1[-1]=(self.h1[-1]+data)[:2000]
        if self._ld: self._text=(self._text+data)[:100000]
    def values(self):
        return {'title':self.title.strip(),'h1':self.h1,'description_present':bool(self.description.strip()),'canonical':self.canonical,'robots':self.robots,'jsonld_blocks':len(self.jsonld),'contact_path':self.contact,'terms_path':self.terms}
    def jsonld_status(self):
        if not self.jsonld: return 'not_applicable'
        try:
            return 'pass' if all(isinstance(json.loads(t),(dict,list)) for t in self.jsonld) else 'fail'
        except (ValueError,RecursionError):
            return 'fail'
