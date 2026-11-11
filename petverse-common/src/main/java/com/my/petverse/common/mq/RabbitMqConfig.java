package com.my.petverse.common.mq;

import lombok.extern.slf4j.Slf4j;
import org.aopalliance.intercept.MethodInterceptor;
import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.FanoutExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.rabbit.config.RetryInterceptorBuilder;
import org.springframework.amqp.rabbit.config.SimpleRabbitListenerContainerFactory;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.retry.RejectAndDontRequeueRecoverer;
import org.springframework.boot.autoconfigure.amqp.RabbitTemplateCustomizer;
import org.springframework.boot.autoconfigure.amqp.SimpleRabbitListenerContainerFactoryConfigurer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * RabbitMQ 公共配置：死信兜底、监听容器与重试策略。
 * <p>
 * 重试语义与原 RocketMQ 保持一致：
 * <ul>
 *   <li>消费方法抛出异常即视为消费失败，容器按退避策略在本地重试；</li>
 *   <li>重试次数对齐原 maxReconsumeTimes（默认 16 次，通知/点赞类消费者 5 次）；</li>
 *   <li>重试耗尽后拒绝消息且不重回队列，经死信交换机转入 petverse.dlq，
 *       对应原 RocketMQ 重试耗尽后进入 %DLQ%&lt;consumerGroup&gt; 的行为，消息不会静默丢失。</li>
 * </ul>
 */
@Slf4j
@Configuration
public class RabbitMqConfig {

    /** 与原 RocketMQ 默认 maxReconsumeTimes 对齐的重试次数 */
    public static final int DEFAULT_MAX_ATTEMPTS = 16;

    /** 与原 maxReconsumeTimes = 5 的消费者对齐的重试次数 */
    public static final int SHORT_MAX_ATTEMPTS = 5;

    /** 默认容器工厂名，各消费者通过 containerFactory 引用 */
    public static final String LISTENER_FACTORY = "mqListenerContainerFactory";

    /** 短重试容器工厂名（通知、点赞类消费者） */
    public static final String SHORT_RETRY_LISTENER_FACTORY = "mqShortRetryListenerContainerFactory";

    /** 死信交换机：所有重试耗尽的消息统一转入，便于集中排查与重放 */
    @Bean
    public FanoutExchange mqDeadLetterExchange() {
        return new FanoutExchange(MqQueues.DEAD_LETTER_EXCHANGE, true, false);
    }

    /** 死信队列：消息体与 x-death 头（原交换机、RoutingKey、失败原因）完整保留 */
    @Bean
    public Queue mqDeadLetterQueue() {
        return QueueBuilder.durable(MqQueues.DEAD_LETTER_QUEUE).build();
    }

    @Bean
    public Binding mqDeadLetterBinding(FanoutExchange mqDeadLetterExchange, Queue mqDeadLetterQueue) {
        return BindingBuilder.bind(mqDeadLetterQueue).to(mqDeadLetterExchange);
    }

    /**
     * 开启 mandatory：消息无法路由到任何队列时由 Broker 退回，
     * 发布器据此判定发送失败并触发调用方的本地兜底，避免"发送成功但无人消费"
     */
    @Bean
    public RabbitTemplateCustomizer mqRabbitTemplateCustomizer() {
        return template -> template.setMandatory(true);
    }

    /** 标准消费者：重试 16 次 */
    @Bean(LISTENER_FACTORY)
    public SimpleRabbitListenerContainerFactory mqListenerContainerFactory(
            SimpleRabbitListenerContainerFactoryConfigurer configurer, ConnectionFactory connectionFactory) {
        return buildFactory(configurer, connectionFactory, DEFAULT_MAX_ATTEMPTS);
    }

    /** 通知/点赞类消费者：与原 maxReconsumeTimes = 5 保持一致 */
    @Bean(SHORT_RETRY_LISTENER_FACTORY)
    public SimpleRabbitListenerContainerFactory mqShortRetryListenerContainerFactory(
            SimpleRabbitListenerContainerFactoryConfigurer configurer, ConnectionFactory connectionFactory) {
        return buildFactory(configurer, connectionFactory, SHORT_MAX_ATTEMPTS);
    }

    private SimpleRabbitListenerContainerFactory buildFactory(
            SimpleRabbitListenerContainerFactoryConfigurer configurer,
            ConnectionFactory connectionFactory, int maxAttempts) {
        SimpleRabbitListenerContainerFactory factory = new SimpleRabbitListenerContainerFactory();
        // 先套用 spring.rabbitmq.listener.simple.* 配置，再覆盖本项目的约定项
        configurer.configure(factory, connectionFactory);
        // 对应原 consumeThreadMax：初始 4 个并发消费者，消息积压时扩容到 16
        factory.setConcurrentConsumers(4);
        factory.setMaxConcurrentConsumers(16);
        // 每次只预取少量消息，保证单条消息处理耗时长时不会在单实例内堆积
        factory.setPrefetchCount(10);
        // 失败消息不重回原队列，避免无限重投；重试由 advice 统一驱动
        factory.setDefaultRequeueRejected(false);
        // 队列尚未声明（如 Broker 首次启动）时不在启动阶段报错退出
        factory.setMissingQueuesFatal(false);
        factory.setAdviceChain(retryAdvice(maxAttempts));
        return factory;
    }

    /**
     * 本地重试：1s 起、2 倍递增、上限 10s 退避；耗尽后抛出拒绝异常，消息转入死信队列
     */
    private MethodInterceptor retryAdvice(int maxAttempts) {
        return RetryInterceptorBuilder.stateless()
                .maxAttempts(maxAttempts)
                .backOffOptions(1000L, 2.0, 10_000L)
                .recoverer(new RejectAndDontRequeueRecoverer())
                .build();
    }
}
