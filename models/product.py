from dataclasses import dataclass, asdict
from typing import Optional
from models.camera_product import CATEGORY_CODES,CATEGORY_SUBCATEGORIES,PACKAGING_PACK_BOXES_UNITS,PACKAGING_PACK_UNITS,PACKAGING_BOX_UNITS,PACKAGING_MODES

def _clean(v):
    if v is None:return ""
    return str(v).strip().upper().replace(" ","_")
def _ostr(v):
    x=_clean(v); return x or None
def _oint(v):
    if v is None:return None
    try:
        if v!=v:return None
    except:pass
    try:return int(float(v))
    except:return None

@dataclass
class Product:
    product_code:str
    categoria:str
    subcategoria:Optional[str]
    producto:str
    packaging_mode:str
    cajas_por_pack:Optional[int]
    unidades_por_pack:Optional[int]
    unidades_por_caja:Optional[int]
    active:bool
    created_at:str
    updated_at:str
    @classmethod
    def create(cls,*,product_code,categoria,subcategoria,producto,packaging_mode,cajas_por_pack,unidades_por_pack,unidades_por_caja,timestamp):
        product_code=_clean(product_code); categoria=_clean(categoria); subcategoria=_ostr(subcategoria); producto=_clean(producto); packaging_mode=_clean(packaging_mode)
        if not product_code: raise ValueError("Código vacío.")
        if categoria not in CATEGORY_CODES: raise ValueError("Categoría inválida.")
        allowed=CATEGORY_SUBCATEGORIES.get(categoria)
        if allowed:
            if not subcategoria or subcategoria not in allowed: raise ValueError("Subcategoría inválida.")
        else: subcategoria=None
        if not producto: raise ValueError("Producto vacío.")
        if packaging_mode not in PACKAGING_MODES: raise ValueError("Modo de empaque inválido.")
        cajas_por_pack=_oint(cajas_por_pack); unidades_por_pack=_oint(unidades_por_pack); unidades_por_caja=_oint(unidades_por_caja)
        if packaging_mode==PACKAGING_PACK_BOXES_UNITS:
            if not cajas_por_pack or not unidades_por_caja: raise ValueError("Completá cajas por pack y unidades por caja.")
            unidades_por_pack=None
        elif packaging_mode==PACKAGING_PACK_UNITS:
            if not unidades_por_pack: raise ValueError("Completá unidades por pack.")
            cajas_por_pack=None; unidades_por_caja=None
        else:
            if not unidades_por_caja: raise ValueError("Completá unidades por caja.")
            cajas_por_pack=None; unidades_por_pack=None
        return cls(product_code,categoria,subcategoria,producto,packaging_mode,cajas_por_pack,unidades_por_pack,unidades_por_caja,True,timestamp,timestamp)
    @classmethod
    def from_row(cls,row):
        g=row.get; active=str(g('active',True)).strip().lower() in {'true','1','yes'}
        return cls(_clean(g('product_code','')),_clean(g('categoria','')),_ostr(g('subcategoria')),_clean(g('producto','')),
                   _clean(g('packaging_mode',PACKAGING_PACK_UNITS)),_oint(g('cajas_por_pack')),_oint(g('unidades_por_pack')),
                   _oint(g('unidades_por_caja')),active,str(g('created_at','')),str(g('updated_at','')))
    def to_row(self): return asdict(self)
    def deactivate_updates(self,*,timestamp):
        if not self.active: raise ValueError("El producto ya está desactivado.")
        return {'active':False,'updated_at':timestamp}
