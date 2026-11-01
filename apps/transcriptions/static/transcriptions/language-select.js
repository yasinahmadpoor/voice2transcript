// ISO 639-1 codes and English names from the iso-codes language catalog.
const RECORDING_LANGUAGES = [
  ["ab", "Abkhazian"],
  ["aa", "Afar"],
  ["af", "Afrikaans"],
  ["ak", "Akan"],
  ["sq", "Albanian"],
  ["am", "Amharic"],
  ["ar", "Arabic"],
  ["an", "Aragonese"],
  ["hy", "Armenian"],
  ["as", "Assamese"],
  ["av", "Avaric"],
  ["ae", "Avestan"],
  ["ay", "Aymara"],
  ["az", "Azerbaijani"],
  ["bm", "Bambara"],
  ["bn", "Bangla"],
  ["ba", "Bashkir"],
  ["eu", "Basque"],
  ["be", "Belarusian"],
  ["bh", "Bihari languages"],
  ["bi", "Bislama"],
  ["nb", "Bokmål, Norwegian; Norwegian Bokmål"],
  ["bs", "Bosnian"],
  ["br", "Breton"],
  ["bg", "Bulgarian"],
  ["my", "Burmese"],
  ["ca", "Catalan; Valencian"],
  ["km", "Central Khmer"],
  ["ch", "Chamorro"],
  ["ce", "Chechen"],
  ["ny", "Chichewa; Chewa; Nyanja"],
  ["zh", "Chinese"],
  ["cu", "Church Slavic; Old Slavonic; Church Slavonic; Old Bulgarian; Old Church Slavonic"],
  ["cv", "Chuvash"],
  ["kw", "Cornish"],
  ["co", "Corsican"],
  ["cr", "Cree"],
  ["hr", "Croatian"],
  ["cs", "Czech"],
  ["da", "Danish"],
  ["dv", "Divehi; Dhivehi; Maldivian"],
  ["nl", "Dutch; Flemish"],
  ["dz", "Dzongkha"],
  ["en", "English"],
  ["eo", "Esperanto"],
  ["et", "Estonian"],
  ["ee", "Ewe"],
  ["fo", "Faroese"],
  ["fj", "Fijian"],
  ["fi", "Finnish"],
  ["fr", "French"],
  ["ff", "Fulah"],
  ["gd", "Gaelic; Scottish Gaelic"],
  ["gl", "Galician"],
  ["lg", "Ganda"],
  ["ka", "Georgian"],
  ["de", "German"],
  ["el", "Greek, Modern (1453-)"],
  ["gn", "Guarani"],
  ["gu", "Gujarati"],
  ["ht", "Haitian; Haitian Creole"],
  ["ha", "Hausa"],
  ["he", "Hebrew"],
  ["hz", "Herero"],
  ["hi", "Hindi"],
  ["ho", "Hiri Motu"],
  ["hu", "Hungarian"],
  ["is", "Icelandic"],
  ["io", "Ido"],
  ["ig", "Igbo"],
  ["id", "Indonesian"],
  ["ia", "Interlingua (International Auxiliary Language Association)"],
  ["ie", "Interlingue; Occidental"],
  ["iu", "Inuktitut"],
  ["ik", "Inupiaq"],
  ["ga", "Irish"],
  ["it", "Italian"],
  ["ja", "Japanese"],
  ["jv", "Javanese"],
  ["kl", "Kalaallisut; Greenlandic"],
  ["kn", "Kannada"],
  ["kr", "Kanuri"],
  ["ks", "Kashmiri"],
  ["kk", "Kazakh"],
  ["ki", "Kikuyu; Gikuyu"],
  ["rw", "Kinyarwanda"],
  ["ky", "Kirghiz; Kyrgyz"],
  ["kv", "Komi"],
  ["kg", "Kongo"],
  ["ko", "Korean"],
  ["kj", "Kuanyama; Kwanyama"],
  ["ku", "Kurdish"],
  ["lo", "Lao"],
  ["la", "Latin"],
  ["lv", "Latvian"],
  ["li", "Limburgan; Limburger; Limburgish"],
  ["ln", "Lingala"],
  ["lt", "Lithuanian"],
  ["lu", "Luba-Katanga"],
  ["lb", "Luxembourgish; Letzeburgesch"],
  ["mk", "Macedonian"],
  ["mg", "Malagasy"],
  ["ms", "Malay"],
  ["ml", "Malayalam"],
  ["mt", "Maltese"],
  ["gv", "Manx"],
  ["mi", "Maori"],
  ["mr", "Marathi"],
  ["mh", "Marshallese"],
  ["mn", "Mongolian"],
  ["na", "Nauru"],
  ["nv", "Navajo; Navaho"],
  ["nd", "Ndebele, North; North Ndebele"],
  ["nr", "Ndebele, South; South Ndebele"],
  ["ng", "Ndonga"],
  ["ne", "Nepali"],
  ["se", "Northern Sami"],
  ["no", "Norwegian"],
  ["nn", "Norwegian Nynorsk; Nynorsk, Norwegian"],
  ["oc", "Occitan (post 1500); Provençal"],
  ["oj", "Ojibwa"],
  ["or", "Oriya"],
  ["om", "Oromo"],
  ["os", "Ossetian; Ossetic"],
  ["pi", "Pali"],
  ["pa", "Panjabi; Punjabi"],
  ["fa", "Persian"],
  ["pl", "Polish"],
  ["pt", "Portuguese"],
  ["ps", "Pushto; Pashto"],
  ["qu", "Quechua"],
  ["ro", "Romanian; Moldavian; Moldovan"],
  ["rm", "Romansh"],
  ["rn", "Rundi"],
  ["ru", "Russian"],
  ["sm", "Samoan"],
  ["sg", "Sango"],
  ["sa", "Sanskrit"],
  ["sc", "Sardinian"],
  ["sr", "Serbian"],
  ["sn", "Shona"],
  ["ii", "Sichuan Yi; Nuosu"],
  ["sd", "Sindhi"],
  ["si", "Sinhala; Sinhalese"],
  ["sk", "Slovak"],
  ["sl", "Slovenian"],
  ["so", "Somali"],
  ["st", "Sotho, Southern"],
  ["es", "Spanish; Castilian"],
  ["su", "Sundanese"],
  ["sw", "Swahili"],
  ["ss", "Swati"],
  ["sv", "Swedish"],
  ["tl", "Tagalog"],
  ["ty", "Tahitian"],
  ["tg", "Tajik"],
  ["ta", "Tamil"],
  ["tt", "Tatar"],
  ["te", "Telugu"],
  ["th", "Thai"],
  ["bo", "Tibetan"],
  ["ti", "Tigrinya"],
  ["to", "Tonga (Tonga Islands)"],
  ["ts", "Tsonga"],
  ["tn", "Tswana"],
  ["tr", "Turkish"],
  ["tk", "Turkmen"],
  ["tw", "Twi"],
  ["ug", "Uighur; Uyghur"],
  ["uk", "Ukrainian"],
  ["ur", "Urdu"],
  ["uz", "Uzbek"],
  ["ve", "Venda"],
  ["vi", "Vietnamese"],
  ["vo", "Volapük"],
  ["wa", "Walloon"],
  ["cy", "Welsh"],
  ["fy", "Western Frisian"],
  ["wo", "Wolof"],
  ["xh", "Xhosa"],
  ["yi", "Yiddish"],
  ["yo", "Yoruba"],
  ["za", "Zhuang; Chuang"],
  ["zu", "Zulu"],
];

class LanguageSelect {
  constructor() {
    this.input = document.getElementById('language-search');
    this.value = document.getElementById('language');
    this.list = document.getElementById('language-options');
    this.toggle = document.getElementById('language-toggle');
    this.status = document.getElementById('language-matches');
    this.options = [];
    this.active = -1;
    const aliases = { fa: 'Farsi', zh: 'Mandarin Chinese', my: 'Burmese Myanmar', km: 'Khmer Cambodian' };
    this.languages = RECORDING_LANGUAGES.map(([code, name]) => {
      let nativeName = '';
      try {
        nativeName = new Intl.DisplayNames([code], { type: 'language' }).of(code);
      } catch { /* English names remain available on browsers without DisplayNames. */ }
      const shortName = name.split(';')[0];
      const label = nativeName && nativeName !== shortName && nativeName !== code
        ? `${shortName} / ${nativeName}` : shortName;
      const terms = [code, label, nativeName, ...name.split(';'), aliases[code] || ''].filter(Boolean);
      return { code, label, terms, search: this.normalize(terms.join(' ')) };
    });
    this.languages.unshift({ code: '', label: 'Auto-detect', terms: ['', 'Auto-detect'], search: 'auto-detect automatic detect' });
    this.input.addEventListener('focus', () => {
      this.input.select();
      this.open('');
    });
    this.input.addEventListener('click', () => {
      if (this.list.hidden) this.open('');
    });
    this.input.addEventListener('input', () => {
      this.value.value = this.resolve(this.input.value).code;
      this.open(this.input.value);
    });
    this.input.addEventListener('keydown', event => this.onKeydown(event));
    this.toggle.addEventListener('click', () => {
      if (this.input.disabled) return;
      const wasOpen = !this.list.hidden;
      this.input.focus();
      if (wasOpen) this.close();
      else this.open('');
    });
    document.getElementById('language-picker').addEventListener('focusout', event => {
      if (!event.currentTarget.contains(event.relatedTarget)) {
        this.setValue(this.value.value);
        this.close();
      }
    });
    this.setValue(this.value.value);
  }

  normalize(value) {
    return value.trim().normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }

  resolve(value) {
    const query = this.normalize(value);
    return this.languages.find(item => item.terms.some(text => this.normalize(text) === query))
      || { code: value.trim(), label: value.trim() };
  }

  setValue(value) {
    const selected = this.resolve(value);
    this.value.value = selected.code;
    this.input.value = selected.label;
    this.close();
  }

  setDisabled(disabled) {
    this.input.disabled = disabled;
    this.toggle.disabled = disabled;
    if (disabled) this.close();
  }

  open(query) {
    if (this.input.disabled) return;
    const search = this.normalize(query);
    this.options = this.languages.filter(item => item.search.includes(search));
    if (search && !this.languages.some(item => item.terms.some(text => this.normalize(text) === search))) {
      this.options.push({ code: query.trim(), label: `Use “${query.trim()}”`, custom: true });
    }
    this.list.replaceChildren(...this.options.map((item, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.tabIndex = -1;
      option.id = `language-option-${index}`;
      option.className = 'language-option';
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(item.code === this.value.value));
      const label = document.createElement('span');
      label.textContent = item.label;
      const code = document.createElement('small');
      code.textContent = item.custom ? 'Custom' : item.code;
      option.append(label, code);
      option.addEventListener('mousedown', event => event.preventDefault());
      option.addEventListener('click', () => this.choose(index));
      return option;
    }));
    this.list.hidden = false;
    this.input.setAttribute('aria-expanded', 'true');
    this.toggle.setAttribute('aria-expanded', 'true');
    this.status.textContent = `${this.options.length} language options available.`;
    this.activate(-1);
  }

  close() {
    this.list.hidden = true;
    this.input.setAttribute('aria-expanded', 'false');
    this.toggle.setAttribute('aria-expanded', 'false');
    this.input.removeAttribute('aria-activedescendant');
    this.active = -1;
  }

  activate(index) {
    this.active = index;
    Array.from(this.list.children).forEach((option, i) => option.setAttribute('data-active', String(i === index)));
    if (index < 0) {
      this.input.removeAttribute('aria-activedescendant');
      return;
    }
    const option = this.list.children[index];
    this.input.setAttribute('aria-activedescendant', option.id);
    option.scrollIntoView({ block: 'nearest' });
  }

  choose(index) {
    const selected = this.options[index];
    if (!selected || this.input.disabled) return;
    this.value.value = selected.code;
    this.input.value = selected.custom ? selected.code : selected.label;
    this.input.focus();
    this.close();
  }

  onKeydown(event) {
    if (this.input.disabled) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      this.close();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const wasClosed = this.list.hidden;
      if (wasClosed) this.open('');
      const count = this.options.length;
      const next = this.active < 0 ? (event.key === 'ArrowDown' ? 0 : count - 1)
        : (this.active + (event.key === 'ArrowDown' ? 1 : -1) + count) % count;
      this.activate(next);
    } else if (event.key === 'Enter' && !this.list.hidden) {
      event.preventDefault();
      this.choose(this.active < 0 ? 0 : this.active);
    } else if (event.key === 'Tab') {
      this.setValue(this.value.value);
    }
  }
}
