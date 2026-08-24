package com.my.petverse.space.feign;

import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.pet.PetExpGainVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

/**
 * 宠物服务 Feign 客户端，通过 Nacos 服务名直连 pet-service
 */
@FeignClient(name = "pet-service")
public interface PetFeignClient {

    /** 按来源为宠物发放经验值 */
    @PostMapping("/pet/exp/grant")
    Result<PetExpGainVO> grantExp(@RequestBody PetExpGrantDTO dto);
}
