package com.my.petverse.shop.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.shop.ProductPageQueryDTO;
import com.my.petverse.common.dto.shop.ProductSaveDTO;
import com.my.petverse.common.dto.shop.ProductUpdateDTO;
import com.my.petverse.common.entity.shop.Product;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.shop.ProductVO;

/**
 * 商品服务接口
 */
public interface ProductService extends IService<Product> {

    /**
     * 商家新增商品（默认下架状态，编辑完成后手动上架）
     *
     * @param userId 商家用户ID
     * @param dto    商品参数
     * @return 商品信息
     */
    ProductVO saveProduct(Long userId, ProductSaveDTO dto);

    /**
     * 商家修改商品（含上下架），仅能操作自己店铺的商品
     *
     * @param userId 商家用户ID
     * @param dto    修改参数
     * @return 商品信息
     */
    ProductVO updateProduct(Long userId, ProductUpdateDTO dto);

    /**
     * 商家删除商品（逻辑删除），仅能操作自己店铺的商品
     *
     * @param userId    商家用户ID
     * @param productId 商品ID
     * @return 是否成功
     */
    boolean deleteProduct(Long userId, Long productId);

    /**
     * 商家分页查询自己店铺的商品（含下架商品）
     *
     * @param userId 商家用户ID
     * @param dto    分页查询参数
     * @return 商品分页结果
     */
    PageResult<ProductVO> pageMine(Long userId, ProductPageQueryDTO dto);

    /**
     * 买家分页浏览商城商品（仅上架商品），聚合店铺名称
     *
     * @param dto 分页查询参数
     * @return 商品分页结果
     */
    PageResult<ProductVO> pageOnSale(ProductPageQueryDTO dto);

    /**
     * 查询商品详情（仅上架商品可见）
     *
     * @param productId 商品ID
     * @return 商品信息
     */
    ProductVO getDetail(Long productId);
}
